"""
Observability 챗봇 에이전트

메모리 아키텍처:
- 단기 (대화 내):    in-process Agent 캐시 + SummarizingConversationManager
- 단기 (재시작 후):  DynamoDB에서 요약본 + 최근 N턴 복원
- 장기 (대화 간):    AgentCore Memory 도구 (agent_core_memory)
"""

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import boto3

# lambda_package 경로 추가
sys.path.insert(0, str(Path(__file__).parent.parent / "lambda_package"))

from strands import Agent
from strands.models import BedrockModel
from strands.agent.conversation_manager import SummarizingConversationManager
from strands_tools.agent_core_memory import AgentCoreMemoryToolProvider

import database as db
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

logger = logging.getLogger(__name__)

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
- **장기 메모리**: 이전 대화에서 기억된 중요 정보 (agent_core_memory)

## 데이터 소스 선택 기준
- 최근 데이터(수 시간~수 일): OpenSearch (fetch_logs, fetch_traces) 우선
- 오래된 이력(수 일 이상) 또는 "지난주", "저번 달" 등: S3/Athena 툴 사용
- 통계/집계 쿼리: query_log_error_summary 활용

## 장기 메모리 사용 원칙
- 대화 시작 시 사용자의 환경/관심사와 관련된 메모리를 agent_core_memory(retrieve)로 조회할 것
- 반복적으로 언급되는 서비스명, 장애 패턴, 환경 설정은 agent_core_memory(record)로 저장할 것

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
- 이 시스템은 OTel Collector → AMP push 방식. Prometheus scrape 방식이 아니므로 `up` 메트릭이 없는 것이 완전히 정상
- 활성 서비스 확인: 반드시 get_active_services 툴을 사용할 것. fetch_amp_metric으로 직접 쿼리하지 말 것

## Lambda 역할 (중요)
- observability-agent Lambda = 알람 발생 시 RCA 분석 + Slack 전송만 담당
- Lambda 실행 기록 없음 = 최근 알람 미발생 또는 concurrency 0 설정 상태
- Lambda와 데이터 수집(AMP/OpenSearch/S3)은 완전히 별개 파이프라인
"""

AWS_REGION = os.environ.get("AWS_REGION_NAME", "ap-northeast-2")
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-3-5-haiku-20241022-v1:0")
AGENTCORE_MEMORY_ID = os.environ.get("AGENTCORE_MEMORY_ID", "")

# 세션별 Agent 인스턴스 캐시 (in-process 캐싱)
_session_agents: dict[str, Agent] = {}


def _build_agent(conversation_id: str) -> Agent:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    system_prompt = f"오늘 날짜: {today} (UTC)\n\n" + SYSTEM_PROMPT

    model = BedrockModel(
        model_id=BEDROCK_MODEL_ID,
        region_name="us-east-1",
    )

    # 컨텍스트 오버플로우 시 자동 요약 (세션 내 안전망)
    conversation_manager = SummarizingConversationManager(
        preserve_recent_messages=20,
        summary_ratio=0.4,
    )

    tools = [
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
    ]
    if AGENTCORE_MEMORY_ID:
        memory_provider = AgentCoreMemoryToolProvider(
            memory_id=AGENTCORE_MEMORY_ID,
            actor_id="observability-chatbot",
            session_id=conversation_id,
            namespace="/incidents/",
            region=AWS_REGION,
        )
        tools.extend(memory_provider.tools)

    return Agent(
        model=model,
        system_prompt=system_prompt,
        tools=tools,
        conversation_manager=conversation_manager,
        callback_handler=None,
    )


def _generate_summary(messages: list[dict]) -> str:
    """Bedrock Converse API로 메시지 목록 요약 생성"""
    bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")
    conversation_text = "\n".join(
        f"[{m['role']}]: {m['content'][:500]}" for m in messages
    )
    response = bedrock.converse(
        modelId=BEDROCK_MODEL_ID,
        messages=[{
            "role": "user",
            "content": [{"text": (
                "다음 대화를 간결하게 요약해주세요. "
                "주요 질문, 조회한 데이터, 발견된 이슈, 핵심 수치를 포함하세요:\n\n"
                + conversation_text
            )}],
        }],
        inferenceConfig={"maxTokens": 800},
    )
    return response["output"]["message"]["content"][0]["text"]


def _maybe_summarize(conversation_id: str) -> None:
    """비요약 메시지가 SUMMARY_THRESHOLD 초과 시 요약 생성 후 DynamoDB 저장"""
    count = db.count_non_summary_messages(conversation_id)
    if count < db.SUMMARY_THRESHOLD:
        return

    # 요약 대상: 최근 RECENT_TURNS*2 개 이전 메시지
    all_messages = [
        m for m in db.get_messages(conversation_id)
        if not m.get("is_summary")
    ]
    to_summarize = all_messages[:-(db.RECENT_TURNS * 2)]
    if not to_summarize:
        return

    try:
        summary_text = _generate_summary(to_summarize)
        db.add_summary(conversation_id, summary_text)
        # 캐시된 agent의 messages도 정리 (컨텍스트 창 정리)
        if conversation_id in _session_agents:
            context = db.get_context_messages(conversation_id)
            _session_agents[conversation_id].messages = [
                {"role": m["role"], "content": [{"text": m["content"]}]}
                for m in context
            ]
    except Exception as e:
        logger.warning("요약 생성 실패 (무시): %s", e)


def delete_session(conversation_id: str) -> None:
    """대화 삭제 시 in-process 캐시 제거"""
    _session_agents.pop(conversation_id, None)


def chat(conversation_id: str, context_messages: list[dict], user_message: str) -> str:
    """
    멀티턴 대화 실행.

    Args:
        conversation_id:  대화 세션 ID
        context_messages: DynamoDB에서 로드한 요약본 + 최근 N턴
        user_message:     현재 사용자 메시지

    Returns:
        에이전트 응답 문자열
    """
    if conversation_id not in _session_agents:
        agent = _build_agent(conversation_id)
        if context_messages:
            agent.messages = [
                {"role": m["role"], "content": [{"text": m["content"]}]}
                for m in context_messages
            ]
        _session_agents[conversation_id] = agent

    response = str(_session_agents[conversation_id](user_message))

    # 메시지 수 초과 시 요약 생성
    _maybe_summarize(conversation_id)

    return response
