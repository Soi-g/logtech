"""
Incident Memory - AOSS 기반 장애 이력 관리
OpenSearch Serverless를 사용한 벡터 검색 및 통계
"""

import os
import json
import boto3
import urllib.request
import urllib.parse
from datetime import datetime

# ============================================================
# 클라이언트 초기화
# ============================================================
bedrock_runtime = boto3.client('bedrock-runtime', region_name='ap-northeast-2')

AOSS_ENDPOINT = os.environ.get('AOSS_INCIDENT_MEMORY_ENDPOINT', '').replace('https://', '')
AWS_REGION = 'ap-northeast-2'


# ============================================================
# 장기 메모리 (OpenSearch Serverless)
# ============================================================
class IncidentMemory:
    """과거 장애 이력 관리 (AOSS 벡터 검색)"""
    
    def __init__(self):
        self.endpoint = AOSS_ENDPOINT
        self.index_name = "incident-memory-index"
    
    def _aoss_request(self, method: str, path: str, body: dict = None, max_retries: int = 3) -> dict:
        """AOSS API 요청 (SigV4 서명 + 재시도)"""
        import time
        
        for attempt in range(max_retries):
            try:
                session = boto3.Session()
                credentials = session.get_credentials().get_frozen_credentials()
                
                from botocore.auth import SigV4Auth
                from botocore.awsrequest import AWSRequest
                
                url = f"https://{self.endpoint}{path}"
                payload = json.dumps(body).encode('utf-8') if body else None
                
                request = AWSRequest(
                    method=method,
                    url=url,
                    data=payload,
                    headers={'Content-Type': 'application/json'} if body else {}
                )
                
                SigV4Auth(credentials, 'aoss', AWS_REGION).add_auth(request)
                
                req = urllib.request.Request(
                    url,
                    data=payload,
                    headers=dict(request.headers),
                    method=method
                )
                
                with urllib.request.urlopen(req, timeout=10) as response:
                    return json.loads(response.read())
            
            except urllib.error.HTTPError as e:
                if e.code == 403 and attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    print(f"⚠️ AOSS 403 오류 (시도 {attempt + 1}/{max_retries}), {wait_time}초 대기 중...")
                    time.sleep(wait_time)
                    continue
                else:
                    raise
            
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    print(f"⚠️ AOSS 요청 실패 (시도 {attempt + 1}/{max_retries}): {e}, {wait_time}초 대기 중...")
                    time.sleep(wait_time)
                    continue
                else:
                    raise
        
        raise Exception(f"AOSS 요청 실패: {max_retries}회 재시도 후 실패")
    
    def search_similar_incidents(self, alert_name: str, error_message: str = "", limit: int = 3) -> list:
        """
        유사 장애 검색 (벡터 + 키워드)
        
        Args:
            alert_name: 알람명
            error_message: 에러 메시지 (벡터 검색용)
            limit: 결과 개수
        """
        try:
            # 벡터 임베딩 생성 (에러 메시지)
            if error_message:
                embedding = self._get_embedding(error_message)
            else:
                embedding = None
            
            # 검색 쿼리 구성
            query = {
                "size": limit,
                "query": {
                    "bool": {
                        "must": [
                            {"term": {"alert_name": alert_name}}
                        ]
                    }
                },
                "sort": [
                    {"timestamp": {"order": "desc"}}
                ]
            }
            
            # 벡터 검색 추가 (유사 에러 패턴)
            if embedding:
                query["query"]["bool"]["should"] = [
                    {
                        "knn": {
                            "log_pattern_vector": {
                                "vector": embedding,
                                "k": limit
                            }
                        }
                    }
                ]
            
            result = self._aoss_request('POST', f'/{self.index_name}/_search', query)
            
            incidents = []
            for hit in result.get('hits', {}).get('hits', []):
                source = hit['_source']
                incidents.append({
                    'incident_id': source.get('incident_id'),
                    'timestamp': source.get('timestamp'),
                    'root_cause': source.get('root_cause'),
                    'resolution': source.get('resolution'),
                    'resolution_time_minutes': source.get('resolution_time_minutes', 0),
                    'similarity_score': hit.get('_score', 0)
                })
            
            return incidents
        
        except Exception as e:
            print(f"⚠️ 유사 장애 검색 실패: {e}")
            return []
    
    def get_stats(self, alert_name: str) -> dict:
        """알람 통계 (resolved 상태만 집계)"""
        try:
            # 집계 쿼리
            query = {
                "size": 0,
                "query": {
                    "bool": {
                        "must": [
                            {"term": {"alert_name": alert_name}},
                            {"term": {"status": "resolved"}}
                        ]
                    }
                },
                "aggs": {
                    "total_count": {"value_count": {"field": "incident_id"}},
                    "avg_resolution_time": {"avg": {"field": "resolution_time_minutes"}},
                    "top_root_causes": {
                        "terms": {"field": "root_cause.keyword", "size": 1}
                    }
                }
            }
            
            result = self._aoss_request('POST', f'/{self.index_name}/_search', query)
            
            aggs = result.get('aggregations', {})
            count = aggs.get('total_count', {}).get('value', 0)
            
            if count == 0:
                return {'count': 0, 'is_new': True}
            
            top_causes = aggs.get('top_root_causes', {}).get('buckets', [])
            most_common = top_causes[0]['key'] if top_causes else "Unknown"
            
            return {
                'count': int(count),
                'is_new': False,
                'avg_resolution_time': aggs.get('avg_resolution_time', {}).get('value', 0),
                'most_common_cause': most_common
            }
        
        except Exception as e:
            print(f"⚠️ 통계 조회 실패: {e}")
            return {'count': 0, 'is_new': True}
    
    def save_incident(self, incident_data: dict):
        """장애 이력 저장"""
        try:
            incident_id = f"{incident_data['alert_name']}_{datetime.utcnow().isoformat()}"
            
            # 에러 메시지 벡터 임베딩
            error_messages = incident_data.get('error_messages', [])
            if error_messages:
                embedding = self._get_embedding(' '.join(error_messages))
            else:
                embedding = [0.0] * 1024
            
            doc = {
                'incident_id': incident_id,
                'alert_name': incident_data['alert_name'],
                'timestamp': incident_data.get('state_change_time', datetime.utcnow().isoformat()),
                'severity': incident_data.get('severity', 'medium'),
                'root_cause': incident_data.get('root_cause', ''),
                'resolution': incident_data.get('resolution', ''),
                'resolution_time_minutes': incident_data.get('resolution_time', 0),
                'metrics': incident_data.get('metrics', {}),
                'log_pattern_vector': embedding,
                'error_messages': error_messages,
                'tags': [incident_data['alert_name'], incident_data.get('severity', 'medium')],
                'status': incident_data.get('status', 'ongoing')
            }
            
            self._aoss_request('POST', f'/{self.index_name}/_doc', doc)
            print(f"✅ 장애 이력 저장: {incident_id} (status: {doc['status']})")
        
        except Exception as e:
            print(f"⚠️ 장애 이력 저장 실패: {e}")
    
    def get_recent_ongoing_incident(self, alert_name: str) -> dict:
        """가장 최근 ongoing 상태 장애 조회"""
        try:
            query = {
                "size": 1,
                "query": {
                    "bool": {
                        "must": [
                            {"term": {"alert_name": alert_name}},
                            {"term": {"status": "ongoing"}}
                        ]
                    }
                },
                "sort": [{"timestamp": {"order": "desc"}}]
            }
            
            result = self._aoss_request('POST', f'/{self.index_name}/_search', query)
            hits = result.get('hits', {}).get('hits', [])
            
            if hits:
                return hits[0]['_source']
            return None
        
        except Exception as e:
            print(f"⚠️ ongoing 장애 조회 실패: {e}")
            return None
    
    def update_incident_resolution(self, incident_id: str, resolution: str, resolution_time_minutes: float):
        """장애 해결 정보 업데이트"""
        try:
            # incident_id로 문서 검색
            query = {
                "query": {
                    "term": {"incident_id.keyword": incident_id}
                }
            }
            
            result = self._aoss_request('POST', f'/{self.index_name}/_search', query)
            hits = result.get('hits', {}).get('hits', [])
            
            if not hits:
                print(f"⚠️ incident_id {incident_id}를 찾을 수 없음")
                return
            
            doc_id = hits[0]['_id']
            
            # 부분 업데이트
            update_doc = {
                "doc": {
                    "resolution": resolution,
                    "resolution_time_minutes": resolution_time_minutes,
                    "status": "resolved",
                    "resolved_at": datetime.utcnow().isoformat()
                }
            }
            
            self._aoss_request('POST', f'/{self.index_name}/_update/{doc_id}', update_doc)
            print(f"✅ 장애 해결 업데이트: {incident_id} ({resolution_time_minutes:.1f}분)")
        
        except Exception as e:
            print(f"⚠️ 장애 해결 업데이트 실패: {e}")
    
    def _get_embedding(self, text: str) -> list:
        """텍스트 임베딩 생성 (Bedrock Titan Embeddings)"""
        try:
            response = bedrock_runtime.invoke_model(
                modelId='amazon.titan-embed-text-v2:0',
                body=json.dumps({"inputText": text})
            )
            
            result = json.loads(response['body'].read())
            return result['embedding']
        
        except Exception as e:
            print(f"⚠️ 임베딩 생성 실패: {e}")
            return [0.0] * 1024
