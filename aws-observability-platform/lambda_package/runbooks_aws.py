"""
Bedrock Knowledge Base 런북 관리
1. S3 업로드 → EventBridge → Lambda → KB Sync 트리거
2. KB Retrieve API를 통한 런북 검색
"""

import os
import json
import boto3

AWS_REGION = os.environ.get("AWS_REGION_NAME", "ap-northeast-2")
KB_ID = os.environ.get("BEDROCK_KB_ID", "")
DATA_SOURCE_ID = os.environ.get("BEDROCK_KB_DATA_SOURCE_ID", "")


def indexing_handler(event, context):
    """
    S3 업로드 이벤트 → Bedrock KB 동기화 트리거
    
    Event: EventBridge S3 Object Created 이벤트
    """
    print(f"이벤트 수신: {json.dumps(event)}")
    
    try:
        # EventBridge 이벤트에서 S3 정보 추출
        detail = event.get("detail", {})
        bucket = detail.get("bucket", {}).get("name", "")
        key = detail.get("object", {}).get("key", "")
        
        print(f"S3 업로드 감지: s3://{bucket}/{key}")
        
        # Bedrock Agent 클라이언트
        bedrock_agent = boto3.client("bedrock-agent", region_name=AWS_REGION)
        
        # Knowledge Base 동기화 시작
        response = bedrock_agent.start_ingestion_job(
            knowledgeBaseId=KB_ID,
            dataSourceId=DATA_SOURCE_ID,
            description=f"Auto sync triggered by {key}"
        )
        
        ingestion_job = response.get("ingestionJob", {})
        job_id = ingestion_job.get("ingestionJobId", "")
        status = ingestion_job.get("status", "")
        
        print(f"✅ KB 동기화 시작: Job ID={job_id}, Status={status}")
        
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "KB sync started",
                "jobId": job_id,
                "status": status,
                "s3Object": f"s3://{bucket}/{key}"
            }, ensure_ascii=False)
        }
        
    except Exception as e:
        print(f"❌ KB 동기화 실패: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}, ensure_ascii=False)
        }


def search_runbook(query: str, n_results: int = 3) -> list[dict]:
    """
    Bedrock Knowledge Base Retrieve API로 런북 검색
    
    Args:
        query: 검색 쿼리
        n_results: 반환할 결과 수
    
    Returns:
        [
            {
                "content": "런북 내용",
                "source": "파일명",
                "section": "섹션명",
                "relevance": 0.85
            },
            ...
        ]
    """
    try:
        bedrock_agent_runtime = boto3.client("bedrock-agent-runtime", region_name=AWS_REGION)
        
        response = bedrock_agent_runtime.retrieve(
            knowledgeBaseId=KB_ID,
            retrievalQuery={"text": query},
            retrievalConfiguration={
                "vectorSearchConfiguration": {
                    "numberOfResults": n_results
                }
            }
        )
        
        results = []
        for item in response.get("retrievalResults", []):
            content = item.get("content", {}).get("text", "")
            location = item.get("location", {})
            s3_location = location.get("s3Location", {})
            score = item.get("score", 0.0)
            
            # S3 URI에서 파일명 추출
            uri = s3_location.get("uri", "")
            source = uri.split("/")[-1] if uri else "unknown"
            
            results.append({
                "content": content,
                "source": source,
                "section": "N/A",  # KB는 섹션 정보를 제공하지 않음
                "relevance": score
            })
        
        print(f"✅ 런북 검색 완료: {len(results)}개 결과")
        return results
        
    except Exception as e:
        print(f"❌ 런북 검색 실패: {e}")
        return []
