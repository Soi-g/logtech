"""
AOSS 인덱스 생성 Lambda
Knowledge Base가 사용할 벡터 인덱스를 미리 생성
"""

import json
import boto3
import urllib.request
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest


def handler(event, context):
    """
    AOSS에 Knowledge Base용 인덱스 생성
    
    Event:
    {
        "endpoint": "https://xxx.aoss.amazonaws.com",
        "region": "ap-northeast-2",
        "index_name": "bedrock-knowledge-base-default-index"
    }
    """
    print(f"이벤트: {json.dumps(event)}")
    
    endpoint = event["endpoint"]
    region = event["region"]
    index_name = event["index_name"]
    
    try:
        session = boto3.Session()
        credentials = session.get_credentials().get_frozen_credentials()
        
        # 인덱스 존재 확인
        check_url = f"{endpoint}/{index_name}"
        check_request = AWSRequest(method="HEAD", url=check_url)
        SigV4Auth(credentials, "aoss", region).add_auth(check_request)
        
        try:
            req = urllib.request.Request(check_url, headers=dict(check_request.headers), method="HEAD")
            urllib.request.urlopen(req, timeout=10)
            print(f"✅ 인덱스 '{index_name}' 이미 존재")
            return {
                "statusCode": 200,
                "body": json.dumps({"status": "exists", "index": index_name})
            }
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
        
        # 인덱스 생성
        body = {
            "settings": {"index.knn": True},
            "mappings": {
                "properties": {
                    "bedrock-knowledge-base-default-vector": {
                        "type": "knn_vector",
                        "dimension": 1024,
                        "method": {"name": "hnsw", "engine": "faiss", "space_type": "l2"}
                    },
                    "AMAZON_BEDROCK_TEXT_CHUNK": {"type": "text"},
                    "AMAZON_BEDROCK_METADATA": {"type": "text", "index": False}
                }
            }
        }
        
        create_url = f"{endpoint}/{index_name}"
        payload = json.dumps(body).encode("utf-8")
        
        create_request = AWSRequest(
            method="PUT",
            url=create_url,
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        SigV4Auth(credentials, "aoss", region).add_auth(create_request)
        
        req = urllib.request.Request(
            create_url,
            data=payload,
            headers=dict(create_request.headers),
            method="PUT"
        )
        response = urllib.request.urlopen(req, timeout=30)
        result = json.loads(response.read())
        
        print(f"✅ 인덱스 '{index_name}' 생성 완료: {result}")
        return {
            "statusCode": 200,
            "body": json.dumps({"status": "created", "index": index_name, "result": result})
        }
        
    except Exception as e:
        print(f"❌ 인덱스 생성 실패: {e}")
        import traceback
        traceback.print_exc()
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }
