"""
AOSS 인덱스 수동 생성 스크립트
로컬에서 실행 (AWS 자격증명 필요)
"""

import json
import boto3
import urllib.request
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

# 설정
INCIDENT_MEMORY_ENDPOINT = "ltqavqsgvthpn1wnpz29.ap-northeast-2.aoss.amazonaws.com"
RUNBOOKS_ENDPOINT = "kfjg5qwyr4gbf3flsqyg.ap-northeast-2.aoss.amazonaws.com"
REGION = "ap-northeast-2"


def create_incident_memory_index():
    """Incident Memory 인덱스 생성"""
    
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
    
    session = boto3.Session()
    credentials = session.get_credentials().get_frozen_credentials()
    
    url = f"https://{INCIDENT_MEMORY_ENDPOINT}/incident-memory-index"
    payload = json.dumps(index_body).encode('utf-8')
    
    request = AWSRequest(
        method='PUT',
        url=url,
        data=payload,
        headers={'Content-Type': 'application/json'}
    )
    
    SigV4Auth(credentials, 'aoss', REGION).add_auth(request)
    
    req = urllib.request.Request(
        url,
        data=payload,
        headers=dict(request.headers),
        method='PUT'
    )
    
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read())
            print(f"✅ Incident Memory 인덱스 생성 성공")
            print(json.dumps(result, indent=2))
            return True
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')
        if 'resource_already_exists' in error_body:
            print(f"✅ Incident Memory 인덱스 이미 존재")
            return True
        else:
            print(f"❌ 오류: {e.code} - {error_body}")
            return False
    except Exception as e:
        print(f"❌ 오류: {e}")
        return False


def create_kb_index():
    """Knowledge Base 인덱스 생성"""
    
    index_body = {
        "settings": {
            "index": {
                "knn": True,
                "knn.algo_param.ef_search": 512
            }
        },
        "mappings": {
            "properties": {
                "AMAZON_BEDROCK_METADATA": {"type": "text", "index": False},
                "AMAZON_BEDROCK_TEXT_CHUNK": {"type": "text"},
                "bedrock-knowledge-base-default-vector": {
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
                }
            }
        }
    }
    
    session = boto3.Session()
    credentials = session.get_credentials().get_frozen_credentials()
    
    url = f"https://{RUNBOOKS_ENDPOINT}/bedrock-knowledge-base-default-index"
    payload = json.dumps(index_body).encode('utf-8')
    
    request = AWSRequest(
        method='PUT',
        url=url,
        data=payload,
        headers={'Content-Type': 'application/json'}
    )
    
    SigV4Auth(credentials, 'aoss', REGION).add_auth(request)
    
    req = urllib.request.Request(
        url,
        data=payload,
        headers=dict(request.headers),
        method='PUT'
    )
    
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read())
            print(f"✅ Knowledge Base 인덱스 생성 성공")
            print(json.dumps(result, indent=2))
            return True
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')
        if 'resource_already_exists' in error_body:
            print(f"✅ Knowledge Base 인덱스 이미 존재")
            return True
        else:
            print(f"❌ 오류: {e.code} - {error_body}")
            return False
    except Exception as e:
        print(f"❌ 오류: {e}")
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("AOSS 인덱스 수동 생성")
    print("=" * 60)
    
    print("\n[1/2] Incident Memory 인덱스 생성 중...")
    success1 = create_incident_memory_index()
    
    print("\n[2/2] Knowledge Base 인덱스 생성 중...")
    success2 = create_kb_index()
    
    print("\n" + "=" * 60)
    if success1 and success2:
        print("✅ 모든 인덱스 생성 완료!")
    else:
        print("⚠️ 일부 인덱스 생성 실패")
    print("=" * 60)
