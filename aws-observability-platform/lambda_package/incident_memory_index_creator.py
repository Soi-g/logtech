"""
OpenSearch Serverless 장애 이력 인덱스 생성
벡터 검색 + 전문 검색 지원
"""

import json
import os
import urllib.request
import urllib.parse
import boto3
from datetime import datetime


def handler(event, context):
    """
    AOSS 장애 이력 인덱스 생성
    
    Event:
        endpoint: AOSS 엔드포인트
        region: AWS 리전
        index_name: 인덱스명
    """
    
    endpoint = event.get("endpoint", "").replace("https://", "")
    region = event.get("region", os.environ.get("AWS_REGION_NAME", "ap-northeast-2"))
    index_name = event.get("index_name", "incident-memory-index")
    
    print(f"인덱스 생성 시작: {index_name}")
    print(f"엔드포인트: {endpoint}")
    
    # 인덱스 스키마 정의
    index_body = {
        "settings": {
            "index": {
                "knn": True,
                "knn.algo_param.ef_search": 512
            }
        },
        "mappings": {
            "properties": {
                # 기본 필드
                "incident_id": {"type": "keyword"},
                "alert_name": {"type": "keyword"},
                "timestamp": {"type": "date"},
                "severity": {"type": "keyword"},
                "status": {"type": "keyword"},  # ongoing or resolved
                "resolved_at": {"type": "date"},
                
                # 분석 결과
                "root_cause": {"type": "text", "analyzer": "standard"},
                "resolution": {"type": "text", "analyzer": "standard"},
                "resolution_time_minutes": {"type": "float"},
                
                # 메트릭 스냅샷
                "metrics": {
                    "properties": {
                        "session_id": {"type": "keyword"},
                        "is_recurring": {"type": "boolean"},
                        "past_occurrences": {"type": "integer"},
                        "jvm_memory_used": {"type": "float"},
                        "cpu_usage": {"type": "float"},
                        "http_error_rate": {"type": "float"}
                    }
                },
                
                # 로그 패턴 (벡터 임베딩)
                "log_pattern_vector": {
                    "type": "knn_vector",
                    "dimension": 1024,
                    "method": {
                        "engine": "faiss",
                        "name": "hnsw",
                        "space_type": "l2",
                        "parameters": {
                            "ef_construction": 512,
                            "m": 16
                        }
                    }
                },
                
                # 에러 메시지 (전문 검색)
                "error_messages": {"type": "text", "analyzer": "standard"},
                
                # 태그
                "tags": {"type": "keyword"}
            }
        }
    }
    
    try:
        # SigV4 서명
        session = boto3.Session()
        credentials = session.get_credentials().get_frozen_credentials()
        
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest
        
        url = f"https://{endpoint}/{index_name}"
        payload = json.dumps(index_body).encode('utf-8')
        
        request = AWSRequest(
            method='PUT',
            url=url,
            data=payload,
            headers={'Content-Type': 'application/json'}
        )
        
        SigV4Auth(credentials, 'aoss', region).add_auth(request)
        
        # 요청 실행
        req = urllib.request.Request(
            url,
            data=payload,
            headers=dict(request.headers),
            method='PUT'
        )
        
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read())
            print(f"✅ 인덱스 생성 성공: {result}")
            
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': '인덱스 생성 완료',
                    'index_name': index_name,
                    'result': result
                })
            }
    
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')
        print(f"❌ HTTP 오류: {e.code} - {error_body}")
        
        # 이미 존재하는 경우는 성공으로 처리
        if e.code == 400 and 'resource_already_exists' in error_body:
            print(f"✅ 인덱스 이미 존재함")
            return {
                'statusCode': 200,
                'body': json.dumps({'message': '인덱스 이미 존재'})
            }
        
        return {
            'statusCode': e.code,
            'body': json.dumps({'error': error_body})
        }
    
    except Exception as e:
        print(f"❌ 오류: {e}")
        import traceback
        traceback.print_exc()
        
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
