#!/usr/bin/env python3
"""
로컬에서 AOSS 인덱스 생성 (Lambda 대신)
AWS 자격증명을 사용하여 직접 AOSS API 호출
"""

import json
import boto3
import urllib.request
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

ENDPOINT = "https://i8vydpv1gge4rlvygm88.ap-northeast-2.aoss.amazonaws.com"
REGION = "ap-northeast-2"
INDEX_NAME = "bedrock-knowledge-base-default-index"


def create_index():
    """AOSS에 Knowledge Base용 인덱스 생성"""
    print(f"엔드포인트: {ENDPOINT}")
    print(f"인덱스명: {INDEX_NAME}")
    
    try:
        session = boto3.Session()
        credentials = session.get_credentials().get_frozen_credentials()
        
        # 인덱스 존재 확인
        print("\n1. 인덱스 존재 여부 확인 중...")
        check_url = f"{ENDPOINT}/{INDEX_NAME}"
        check_request = AWSRequest(method="HEAD", url=check_url)
        SigV4Auth(credentials, "aoss", REGION).add_auth(check_request)
        
        try:
            req = urllib.request.Request(check_url, headers=dict(check_request.headers), method="HEAD")
            urllib.request.urlopen(req, timeout=10)
            print(f"✅ 인덱스 '{INDEX_NAME}' 이미 존재합니다")
            return True
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print(f"인덱스가 없습니다. 생성을 시작합니다...")
            elif e.code == 403:
                print(f"❌ 403 Forbidden - AOSS 접근 권한이 없습니다")
                print(f"현재 AWS 자격증명 확인:")
                print(f"  Access Key: {credentials.access_key[:10]}...")
                print(f"  계정 ID 확인 중...")
                sts = boto3.client('sts')
                identity = sts.get_caller_identity()
                print(f"  계정: {identity['Account']}")
                print(f"  ARN: {identity['Arn']}")
                print(f"\nAOSS 정책에 이 역할/사용자가 포함되어 있는지 확인하세요.")
                return False
            else:
                raise
        
        # 인덱스 생성
        print("\n2. 인덱스 생성 중...")
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
        
        create_url = f"{ENDPOINT}/{INDEX_NAME}"
        payload = json.dumps(body).encode("utf-8")
        
        create_request = AWSRequest(
            method="PUT",
            url=create_url,
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        SigV4Auth(credentials, "aoss", REGION).add_auth(create_request)
        
        req = urllib.request.Request(
            create_url,
            data=payload,
            headers=dict(create_request.headers),
            method="PUT"
        )
        response = urllib.request.urlopen(req, timeout=30)
        result = json.loads(response.read())
        
        print(f"✅ 인덱스 '{INDEX_NAME}' 생성 완료!")
        print(f"응답: {json.dumps(result, indent=2)}")
        return True
        
    except urllib.error.HTTPError as e:
        print(f"❌ HTTP 오류: {e.code} {e.reason}")
        try:
            error_body = e.read().decode('utf-8')
            print(f"오류 상세: {error_body}")
        except:
            pass
        return False
    except Exception as e:
        print(f"❌ 인덱스 생성 실패: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("AOSS 인덱스 생성 스크립트")
    print("=" * 60)
    
    success = create_index()
    
    if success:
        print("\n✅ 완료! 이제 terraform에서 KB 리소스 주석을 해제하고 apply 하세요.")
    else:
        print("\n❌ 실패. AOSS 정책 전파를 더 기다리거나 권한을 확인하세요.")
