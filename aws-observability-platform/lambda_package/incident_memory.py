"""
Incident Memory - AOSS 기반 장애 이력 관리
opensearch-py + requests-aws4auth 사용
"""

import os
import json
import boto3
from datetime import datetime

AWS_REGION = os.environ.get('AWS_REGION_NAME', 'ap-northeast-2')
AOSS_ENDPOINT = os.environ.get('AOSS_INCIDENT_MEMORY_ENDPOINT', '').replace('https://', '')

bedrock_runtime = boto3.client('bedrock-runtime', region_name=AWS_REGION)


class IncidentMemory:
    """과거 장애 이력 관리 (AOSS 벡터 검색)"""

    def __init__(self):
        self.endpoint = AOSS_ENDPOINT
        self.index_name = "incident-memory-index"
        self._client = None

    def _get_client(self):
        if self._client is None:
            from opensearchpy import OpenSearch, RequestsHttpConnection
            from requests_aws4auth import AWS4Auth

            session = boto3.Session()
            credentials = session.get_credentials().get_frozen_credentials()

            auth = AWS4Auth(
                credentials.access_key,
                credentials.secret_key,
                AWS_REGION,
                'aoss',
                session_token=credentials.token
            )

            self._client = OpenSearch(
                hosts=[{'host': self.endpoint, 'port': 443}],
                http_auth=auth,
                use_ssl=True,
                verify_certs=True,
                connection_class=RequestsHttpConnection,
                pool_maxsize=20
            )
        return self._client

    def search_similar_incidents(self, alert_name: str, error_message: str = "", limit: int = 3) -> list:
        """유사 장애 검색"""
        try:
            client = self._get_client()

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

            if error_message:
                embedding = self._get_embedding(error_message)
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

            result = client.search(index=self.index_name, body=query)

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
        """알람 통계"""
        try:
            client = self._get_client()

            query = {
                "size": 0,
                "query": {
                    "bool": {
                        "must": [
                            {"term": {"alert_name": alert_name}}
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

            result = client.search(index=self.index_name, body=query)

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
            client = self._get_client()

            incident_id = f"{incident_data['alert_name']}_{datetime.utcnow().isoformat()}"

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

            client.index(index=self.index_name, body=doc)
            print(f"✅ 장애 이력 저장: {incident_id} (status: {doc['status']})")

        except Exception as e:
            print(f"⚠️ 장애 이력 저장 실패: {e}")

    def get_recent_ongoing_incident(self, alert_name: str) -> dict:
        """가장 최근 ongoing 상태 장애 조회"""
        try:
            client = self._get_client()

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

            result = client.search(index=self.index_name, body=query)
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
            client = self._get_client()

            query = {
                "query": {
                    "term": {"incident_id.keyword": incident_id}
                }
            }

            result = client.search(index=self.index_name, body=query)
            hits = result.get('hits', {}).get('hits', [])

            if not hits:
                print(f"⚠️ incident_id {incident_id}를 찾을 수 없음")
                return

            doc_id = hits[0]['_id']

            update_doc = {
                "doc": {
                    "resolution": resolution,
                    "resolution_time_minutes": resolution_time_minutes,
                    "status": "resolved",
                    "resolved_at": datetime.utcnow().isoformat()
                }
            }

            client.update(index=self.index_name, id=doc_id, body=update_doc)
            print(f"✅ 장애 해결 업데이트: {incident_id} ({resolution_time_minutes:.1f}분)")

        except Exception as e:
            print(f"⚠️ 장애 해결 업데이트 실패: {e}")

    def _get_embedding(self, text: str) -> list:
        """텍스트 임베딩 생성"""
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