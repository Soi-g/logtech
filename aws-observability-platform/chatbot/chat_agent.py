"""
Observability 챗봇 에이전트
- Strands Agent 단일 에이전트 (RCA 루프 없음)
- agents_aws.py 툴 재사용
- 대화 히스토리 포함해서 멀티턴 대화 지원
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# lambda_package 경로 추가
sys.path.insert(0, str(Path(__file__).parent.parent / "lambda_package"))

from strands import Agent
from strands.models import BedrockModel

from agents_aws import (
    fetch_amp_metric,
    fetch_logs,
    fetch_traces,
    fetch_ec2_status,
    fetch_rds_status,
    fetch_cloudwatch_metric,
    fetch_cloudwatch_alarms,
    fetch_cloudwatch_logs,
    fetch_cloudtrail_events,
    fetch_elb_health,
    fetch_autoscaling_activity,
    search_incident_history,
    get_ongoing_alarms,
    get_active_services,
    query_historical_logs,
    query_historical_traces,
    query_log_error_summary,
)

SYSTEM_PROMPT = """당신은 AWS 옵저버빌리티 플랫폼의 AI 어시스턴트입니다.
사용자의 질문에 따라 적절한 툴을 사용해 메트릭, 로그, 트레이스, 인프라 상태, 인시던트 이력을 조회하고 한국어로 답변합니다.

## 사용 가능한 데이터 소스
- **AMP (Prometheus)**: OTel 앱/JVM/컨테이너 메트릭 (fetch_amp_metric)
- **OpenSearch**: 애플리케이션 로그 (fetch_logs), 분산 트레이스 (fetch_traces)
- **CloudWatch**: AWS 인프라 메트릭/알람 (fetch_cloudwatch_metric, fetch_cloudwatch_alarms)
- **EC2/RDS**: 인프라 상태 (fetch_ec2_status, fetch_rds_status)
- **ELB/ASG**: 로드밸런서/오토스케일링 (fetch_elb_health, fetch_autoscaling_activity)
- **인시던트 이력**: 과거 장애 기록 (search_incident_history)
- **현재 알람**: 발화 중인 알람 목록 (get_ongoing_alarms)
- **활성 서비스**: 현재 메트릭 전송 중인 서비스 (get_active_services)
- **S3/Athena (장기 이력)**: OpenSearch보다 오래된 로그/트레이스 이력 조회 (query_historical_logs, query_historical_traces, query_log_error_summary)

## 데이터 소스 선택 기준
- 최근 데이터(수 시간~수 일): OpenSearch (fetch_logs, fetch_traces) 우선
- 오래된 이력(수 일 이상) 또는 "지난주", "저번 달" 등: S3/Athena 툴 사용
- 통계/집계 쿼리: query_log_error_summary 활용

## 답변 원칙
- 툴에서 가져온 실제 데이터를 기반으로 답변할 것
- 데이터가 없을 때 이유를 구분해서 해석할 것:
    · `up` 메트릭 없음 → 정상. OTel push 방식은 up 메트릭을 생성하지 않음. 이슈/문제로 절대 언급하지 말 것
    · Lambda 실행 없음 → 최근 알람이 없었거나 concurrency 0 상태. 데이터 수집과 무관
    · 로그/트레이스 없음 → 앱이 꺼져 있거나 해당 시간대에 트래픽이 없었던 것
    · 메트릭 없음 → 앱이 꺼져 있거나 OTel Collector가 중단된 것. Lambda와 무관
- 수치는 구체적으로 제시할 것 (예: "에러율 3.2%", "P99 1.45초")
- 필요 시 추가 조회 툴을 연속으로 호출할 것

## AMP 라벨 규칙
- deployment_environment: "dev" = VM/Docker, "prod" = EC2/AWS
- job 라벨 = "namespace/service" 형태 (예: "todolist/springboot")
- 이 시스템은 OTel Collector → AMP push 방식. Prometheus scrape 방식이 아니므로 `up` 메트릭이 없는 것이 완전히 정상. 답변에서 up 메트릭 부재를 이슈/문제/여전한 이슈로 표현하지 말 것
- 활성 서비스 확인: 반드시 get_active_services 툴을 사용할 것. fetch_amp_metric으로 직접 쿼리하지 말 것

## Lambda 역할 (중요)
- observability-agent Lambda = 알람 발생 시 RCA 분석 + Slack 전송만 담당
- Lambda 실행 기록 없음 = 최근 알람 미발생 또는 concurrency 0 설정 상태
- Lambda와 데이터 수집(AMP/OpenSearch/S3)은 완전히 별개 파이프라인. Lambda 상태가 데이터 수집에 영향 없음
"""

AWS_REGION = os.environ.get("AWS_REGION_NAME", "ap-northeast-2")
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5")


def _build_agent() -> Agent:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    system_prompt = f"오늘 날짜: {today} (UTC)\n\n" + SYSTEM_PROMPT

    model = BedrockModel(
        model_id=BEDROCK_MODEL_ID,
        region_name=AWS_REGION,
    )
    return Agent(
        model=model,
        system_prompt=system_prompt,
        tools=[
            fetch_amp_metric,
            fetch_logs,
            fetch_traces,
            fetch_ec2_status,
            fetch_rds_status,
            fetch_cloudwatch_metric,
            fetch_cloudwatch_alarms,
            fetch_cloudwatch_logs,
            fetch_cloudtrail_events,
            fetch_elb_health,
            fetch_autoscaling_activity,
            search_incident_history,
            get_ongoing_alarms,
            get_active_services,
            query_historical_logs,
            query_historical_traces,
            query_log_error_summary,
        ],
        callback_handler=None,
    )


def chat(messages: list[dict], user_message: str) -> str:
    """
    멀티턴 대화 실행.

    Args:
        messages: 이전 대화 히스토리 [{"role": "user"|"assistant", "content": "..."}]
        user_message: 현재 사용자 메시지

    Returns:
        에이전트 응답 문자열
    """
    agent = _build_agent()

    # 히스토리 포함한 전체 프롬프트 구성
    history_text = ""
    for msg in messages:
        role = "사용자" if msg["role"] == "user" else "어시스턴트"
        history_text += f"{role}: {msg['content']}\n\n"

    if history_text:
        prompt = f"## 이전 대화\n{history_text}## 현재 질문\n사용자: {user_message}"
    else:
        prompt = user_message

    result = agent(prompt)
    return str(result)
