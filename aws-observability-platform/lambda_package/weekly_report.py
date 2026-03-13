"""
주간 옵저버빌리티 리포트
매주 월요일 오전 9시에 지난 주 데이터를 분석하여 Slack으로 전송
"""

import os
import json
import time
import urllib.request
import boto3
from datetime import datetime, timedelta, timezone

AWS_REGION = os.environ.get("AWS_REGION_NAME", "ap-northeast-2")
ATHENA_DATABASE = os.environ.get("GLUE_DATABASE", "log_platform_dev_observability")
ATHENA_OUTPUT = os.environ.get("ATHENA_OUTPUT_LOCATION", "s3://log-platform-dev-athena-results/")
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL", "")

athena = boto3.client("athena", region_name=AWS_REGION)


def execute_athena_query(query: str, wait: bool = True) -> dict:
    """Athena 쿼리 실행 및 결과 반환"""
    print(f"Athena 쿼리 실행: {query[:100]}...")
    
    response = athena.start_query_execution(
        QueryString=query,
        QueryExecutionContext={"Database": ATHENA_DATABASE},
        ResultConfiguration={"OutputLocation": ATHENA_OUTPUT}
    )
    
    query_execution_id = response["QueryExecutionId"]
    
    if not wait:
        return {"execution_id": query_execution_id}
    
    # 쿼리 완료 대기
    max_wait = 60  # 최대 60초
    elapsed = 0
    while elapsed < max_wait:
        status = athena.get_query_execution(QueryExecutionId=query_execution_id)
        state = status["QueryExecution"]["Status"]["State"]
        
        if state == "SUCCEEDED":
            break
        elif state in ["FAILED", "CANCELLED"]:
            reason = status["QueryExecution"]["Status"].get("StateChangeReason", "Unknown")
            raise Exception(f"Athena 쿼리 실패: {reason}")
        
        time.sleep(2)
        elapsed += 2
    
    # 결과 조회
    results = athena.get_query_results(QueryExecutionId=query_execution_id)
    
    # 파싱
    rows = results["ResultSet"]["Rows"]
    if len(rows) <= 1:
        return []
    
    headers = [col["VarCharValue"] for col in rows[0]["Data"]]
    data = []
    for row in rows[1:]:
        values = [col.get("VarCharValue", "") for col in row["Data"]]
        data.append(dict(zip(headers, values)))
    
    return data


def get_top_errors(days: int = 7) -> list:
    """지난 N일간 Top 에러"""
    query = f"""
    SELECT 
        message,
        COUNT(*) as error_count
    FROM logs
    WHERE dt >= date_format(current_date - interval '{days}' day, '%Y-%m-%d')
      AND level = 'ERROR'
    GROUP BY message
    ORDER BY error_count DESC
    LIMIT 5
    """
    
    try:
        return execute_athena_query(query)
    except Exception as e:
        print(f"Top 에러 조회 실패: {e}")
        return []


def get_slow_endpoints(days: int = 7) -> list:
    """지난 N일간 느린 엔드포인트"""
    query = f"""
    SELECT 
        http_target as endpoint,
        approx_percentile(duration_ms, 0.95) as p95_ms,
        COUNT(*) as request_count
    FROM traces
    WHERE dt >= date_format(current_date - interval '{days}' day, '%Y-%m-%d')
      AND span_kind = 'SERVER'
    GROUP BY http_target
    HAVING p95_ms > 1000
    ORDER BY p95_ms DESC
    LIMIT 5
    """
    
    try:
        return execute_athena_query(query)
    except Exception as e:
        print(f"느린 엔드포인트 조회 실패: {e}")
        return []


def get_service_health(days: int = 7) -> list:
    """서비스별 건강도"""
    query = f"""
    SELECT 
        service_name,
        COUNT(*) as total_requests,
        SUM(CASE WHEN status_code >= 500 THEN 1 ELSE 0 END) as error_count,
        CAST(SUM(CASE WHEN status_code >= 500 THEN 1 ELSE 0 END) AS DOUBLE) / COUNT(*) * 100 as error_rate
    FROM traces
    WHERE dt >= date_format(current_date - interval '{days}' day, '%Y-%m-%d')
      AND span_kind = 'SERVER'
    GROUP BY service_name
    ORDER BY error_rate DESC
    LIMIT 5
    """
    
    try:
        return execute_athena_query(query)
    except Exception as e:
        print(f"서비스 건강도 조회 실패: {e}")
        return []


def send_slack_report(report_data: dict):
    """Slack으로 리포트 전송"""
    
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "📊 주간 옵저버빌리티 리포트"}
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"기간: {report_data['period']} | 생성: {report_data['generated_at']}"}
            ]
        },
        {"type": "divider"}
    ]
    
    # Top 에러
    if report_data.get("top_errors"):
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*🔴 Top 5 에러*"}
        })
        
        error_lines = []
        for i, err in enumerate(report_data["top_errors"][:5], 1):
            msg = err.get("message", "Unknown")[:100]
            count = err.get("error_count", 0)
            error_lines.append(f"{i}. `{msg}` - {count}회")
        
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(error_lines) if error_lines else "_에러 없음_"}
        })
    
    blocks.append({"type": "divider"})
    
    # 느린 엔드포인트
    if report_data.get("slow_endpoints"):
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*🐌 느린 엔드포인트 (P95 > 1초)*"}
        })
        
        slow_lines = []
        for i, ep in enumerate(report_data["slow_endpoints"][:5], 1):
            endpoint = ep.get("endpoint", "Unknown")
            p95 = float(ep.get("p95_ms", 0))
            count = ep.get("request_count", 0)
            slow_lines.append(f"{i}. `{endpoint}` - P95: {p95:.0f}ms ({count}회)")
        
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(slow_lines) if slow_lines else "_느린 엔드포인트 없음_"}
        })
    
    blocks.append({"type": "divider"})
    
    # 서비스 건강도
    if report_data.get("service_health"):
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*💚 서비스 건강도*"}
        })
        
        health_lines = []
        for svc in report_data["service_health"][:5]:
            name = svc.get("service_name", "Unknown")
            error_rate = float(svc.get("error_rate", 0))
            total = svc.get("total_requests", 0)
            
            emoji = "🟢" if error_rate < 1 else "🟡" if error_rate < 5 else "🔴"
            health_lines.append(f"{emoji} `{name}` - 에러율: {error_rate:.2f}% ({total}회)")
        
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(health_lines) if health_lines else "_데이터 없음_"}
        })
    
    blocks.append({"type": "divider"})
    
    # 권장 사항
    recommendations = []
    if report_data.get("top_errors") and len(report_data["top_errors"]) > 0:
        recommendations.append("• 반복되는 에러 패턴 분석 필요")
    if report_data.get("slow_endpoints") and len(report_data["slow_endpoints"]) > 0:
        recommendations.append("• 느린 엔드포인트 성능 최적화 검토")
    
    if recommendations:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*💡 권장 사항*\n" + "\n".join(recommendations)}
        })
    
    # Slack 전송
    payload = {
        "channel": SLACK_CHANNEL,
        "blocks": blocks,
        "text": "주간 옵저버빌리티 리포트"
    }
    
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        },
        method="POST"
    )
    
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read())
        if result.get("ok"):
            print("✅ Slack 리포트 전송 완료")
        else:
            print(f"❌ Slack 전송 실패: {result.get('error')}")


def handler(event, context):
    """Lambda 핸들러"""
    print("주간 리포트 생성 시작")
    
    # 지난 주 기간 계산
    today = datetime.now(timezone.utc)
    last_week_start = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    last_week_end = today.strftime("%Y-%m-%d")
    
    # 데이터 수집
    report_data = {
        "period": f"{last_week_start} ~ {last_week_end}",
        "generated_at": today.strftime("%Y-%m-%d %H:%M UTC"),
        "top_errors": get_top_errors(7),
        "slow_endpoints": get_slow_endpoints(7),
        "service_health": get_service_health(7)
    }
    
    print(f"리포트 데이터: {json.dumps(report_data, indent=2, ensure_ascii=False)}")
    
    # Slack 전송
    send_slack_report(report_data)
    
    return {
        "statusCode": 200,
        "body": json.dumps({"message": "주간 리포트 전송 완료"}, ensure_ascii=False)
    }


if __name__ == "__main__":
    # 로컬 테스트
    handler({}, {})
