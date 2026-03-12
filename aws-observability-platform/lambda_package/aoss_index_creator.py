"""
AOSS 인덱스 생성 Lambda
requests-aws4auth + opensearch-py 사용
"""

import json
import boto3


def handler(event, context):
    print(f"이벤트: {json.dumps(event)}")

    endpoint = event["endpoint"].replace("https://", "")
    region = event.get("region", "ap-northeast-2")
    index_name = event["index_name"]

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
                "statusCode": 200,
                "body": json.dumps({"status": "exists", "index": index_name})
            }
        except Exception as e:
            if "404" not in str(e) and "index_not_found" not in str(e).lower():
                raise

        # 인덱스 생성
        body = {
            "settings": {
                "index.knn": True,
                "index.knn.algo_param.ef_search": 512
            },
            "mappings": {
                "properties": {
                    "bedrock-knowledge-base-default-vector": {
                        "type": "knn_vector",
                        "dimension": 1024,
                        "method": {
                            "name": "hnsw",
                            "engine": "faiss",
                            "space_type": "l2",
                            "parameters": {"ef_construction": 512, "m": 16}
                        }
                    },
                    "AMAZON_BEDROCK_TEXT_CHUNK": {"type": "text"},
                    "AMAZON_BEDROCK_METADATA": {"type": "text", "index": False}
                }
            }
        }

        result = client.indices.create(index=index_name, body=body)
        print(f"✅ 인덱스 생성 완료: {result}")

        return {
            "statusCode": 200,
            "body": json.dumps({"status": "created", "index": index_name})
        }

    except Exception as e:
        print(f"❌ 실패: {e}")
        import traceback
        traceback.print_exc()
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }