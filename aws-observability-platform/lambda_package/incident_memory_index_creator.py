"""
AOSS 장애 이력 인덱스 생성 Lambda
opensearch-py + requests-aws4auth 사용
"""

import json
import os
import boto3


def handler(event, context):
    endpoint = event.get("endpoint", "").replace("https://", "")
    region = event.get("region", os.environ.get("AWS_REGION_NAME", "ap-northeast-2"))
    index_name = event.get("index_name", "incident-memory-index")

    print(f"인덱스 생성 시작: {index_name}")
    print(f"엔드포인트: {endpoint}")

    try:
        from opensearchpy import OpenSearch, RequestsHttpConnection
        from requests_aws4auth import AWS4Auth

        session = boto3.Session()
        credentials = session.get_credentials().get_frozen_credentials()

        auth = AWS4Auth(
            credentials.access_key,
            credentials.secret_key,
            region,
            'aoss',
            session_token=credentials.token
        )

        client = OpenSearch(
            hosts=[{'host': endpoint, 'port': 443}],
            http_auth=auth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
            pool_maxsize=20
        )

        # 인덱스 존재 확인 (GET 방식 - AOSS는 HEAD 미지원)
        try:
            client.indices.get(index=index_name)
            print(f"✅ 인덱스 '{index_name}' 이미 존재")
            return {
                'statusCode': 200,
                'body': json.dumps({'message': '인덱스 이미 존재', 'index_name': index_name})
            }
        except Exception as e:
            if "404" not in str(e) and "index_not_found" not in str(e).lower():
                raise

        # 인덱스 생성
        index_body = {
            "settings": {
                "index": {
                    "knn": True,
                    "knn.algo_param.ef_search": 512
                }
            },
            "mappings": {
                "properties": {
                    "incident_id": {"type": "keyword"},
                    "alert_name": {"type": "keyword"},
                    "timestamp": {"type": "date"},
                    "severity": {"type": "keyword"},
                    "status": {"type": "keyword"},
                    "resolved_at": {"type": "date"},
                    "root_cause": {"type": "text", "analyzer": "standard"},
                    "resolution": {"type": "text", "analyzer": "standard"},
                    "resolution_time_minutes": {"type": "float"},
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
                    "error_messages": {"type": "text", "analyzer": "standard"},
                    "tags": {"type": "keyword"}
                }
            }
        }

        result = client.indices.create(index=index_name, body=index_body)
        print(f"✅ 인덱스 생성 성공: {result}")

        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': '인덱스 생성 완료',
                'index_name': index_name,
                'result': result
            })
        }

    except Exception as e:
        print(f"❌ 오류: {e}")
        import traceback
        traceback.print_exc()
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }