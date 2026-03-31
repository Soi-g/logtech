"""
AWS Observability Platform - Graph Agent with Memory

obs-agent 스타일 Collector→Writer→Reviewer 루프 적용:
- COLLECTOR_PROMPT: 단계별 조사 전략, 환경 감지, cross-validation
- WRITER_PROMPT: 엄격한 JSON 출력 + evidence rules
- REVIEWER_PROMPT: PASS/FAIL 품질 검증 (최대 3회 반복)

메모리:
- AgentCore: 장기 이력 저장/조회
- DynamoDB: ongoing 인시던트 상태 추적
"""

import json
import logging
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Annotated, TypedDict

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
logger = logging.getLogger(__name__)

try:
    from cw_logger import cw_log
except Exception:
    def cw_log(msg): logger.info(msg)

from langgraph.graph import END, StateGraph
from strands import Agent
from strands.models import BedrockModel

from agents_aws import infrastructure_agent, logs_agent, metrics_agent, clear_raw_outputs, get_raw_outputs
from agentcore_memory import AgentCoreMemory
from dynamodb_incident import get_ongoing_incident, put_ongoing_incident
from topology_prompt import TOPOLOGY_CONTEXT

# Collector + sub-agent(metrics/logs/infra): Sonnet 4 — tool_use 안정성 확보
MODEL_ID        = os.environ.get("ANALYSIS_MODEL_ID", "apac.anthropic.claude-sonnet-4-20250514-v1:0")
# Writer + Reviewer: Haiku — 구조화된 JSON 출력만 담당, 비용 절감
WRITER_MODEL_ID = os.environ.get("WRITER_MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")
AWS_REGION      = os.environ.get("AWS_REGION_NAME", "ap-northeast-2")
MAX_ITERATIONS  = 3
RCA_TIMEOUT_SECONDS = 200  # Lambda 300s timeout 기준 여유 확보

model        = BedrockModel(model_id=MODEL_ID,        region_name=AWS_REGION, streaming=False)
writer_model = BedrockModel(model_id=WRITER_MODEL_ID, region_name=AWS_REGION, streaming=False)


# ============================================================
# State 정의
# ============================================================
def _last(x, y):
    return y


class ObservabilityState(TypedDict):
    question:          Annotated[str, _last]
    alert_name:        Annotated[str, _last]
    severity:          Annotated[str, _last]
    amp_link:          Annotated[str, _last]
    category:          Annotated[list, _last]
    memory_stats:      Annotated[dict, _last]
    similar_incidents: Annotated[list, _last]
    final_answer:      Annotated[str, _last]
    session_id:        Annotated[str, _last]
    state_change_time: Annotated[str, _last]
    slack_ts:          Annotated[str, _last]
    slack_channel:     Annotated[str, _last]


# ============================================================
# obs-agent 스타일 프롬프트
# ============================================================

COLLECTOR_PROMPT = f"""You are an SRE Information Collector for an observability platform.

Your job: gather evidence about the incident from the alarm description. Follow the evidence — do NOT guess.

## CORE RULE: 모든 tool 호출 전에 근거를 먼저 확인하라

tool을 호출하기 전에 반드시 다음 질문에 답할 수 있어야 한다:
"알람 설명 또는 이전 tool 결과의 어떤 내용이 이 호출을 정당화하는가?"

답할 수 없으면 호출하지 마라. "혹시 모르니까", "확인 차원에서"는 호출 이유가 아니다.

## NUMBERS RULE (ABSOLUTE)
- tool이 반환한 수치·타임스탬프·상태값만 기재할 것
- tool 응답에 없는 숫자·시각·상태를 추론하거나 보완하는 것은 엄격히 금지
- tool 응답의 수치를 요약하거나 재해석하지 말고 그대로 인용할 것
- tool이 정상 수치를 반환했으면 해당 항목은 정상으로 결론 내릴 것 — 정상 데이터를 무시하고 이상을 지어내는 것은 엄격히 금지

## Tools
- metrics_agent       : AMP(OTel) + CloudWatch 메트릭 조회 (앱/JVM/컨테이너/RDS/EC2)
- logs_agent          : OpenSearch 로그 + 트레이스 조회 + CloudWatch Logs
  **logs_agent는 앱 로그(logs-app)만 봅니다. 인프라/OS 로그가 나오면 무시하세요.**
  **트레이스 조회가 필요하면 query에 "트레이스" 또는 "traces" 키워드를 포함하라 — logs_agent가 자동으로 fetch_traces를 선택한다.**
- infrastructure_agent : AWS RDS/EC2 현재 상태 직접 조회 (prod 환경만)

## 환경(deployment_environment) 처리 규칙
- `deployment_environment=dev` → dev 환경만 조회
- `deployment_environment=prod` → prod 환경만 조회
- `deployment_environment=both` → dev와 prod 둘 다 조회. 각 에이전트 호출 시 쿼리에 환경을 명시하여 dev/prod 각각 호출하라. 결과는 환경별로 구분하여 보고하라.

## 트레이스 조회 규칙
- 사용자가 "트레이스", "traces", "span" 조회를 명시 요청하면 → logs_agent 쿼리에 "트레이스" 키워드 포함하여 호출
- 사용자가 트레이스 불필요 명시 시("트레이스는 필요 없어" 등) → 호출 생략
{TOPOLOGY_CONTEXT}

## PHASE 1 — 알람 정보만으로 정당화되는 조사는 첫 턴에 병렬 호출

알람 설명 자체가 여러 에이전트 호출을 동시에 정당화하면 첫 응답에서 모두 호출하라.
에이전트 B의 호출이 에이전트 A의 결과에 의존하지 않는다면 반드시 병렬로 호출하라.

증상별 초기 병렬 호출 기준:
- App 에러 / 서비스다운  → metrics_agent(에러율·latency) + logs_agent(ERROR 로그) 동시 호출
- ServiceDown →
    STEP 1: 알람 발화 시각(## 알람 발화 시각 섹션의 state_change_time) 기준 ±15분 구간으로
            metrics_agent(HTTP 메트릭 수신 여부) + logs_agent(ERROR/종료 로그) 동시 호출
            ※ 현재 메트릭이 정상이어도 발화 시각 구간 데이터를 반드시 확인할 것
    STEP 2: 발화 시각 구간에 메트릭 공백(absent 또는 0) 확인
            → 공백 있으면: 실제 ServiceDown (이후 복구됨) → 다운 원인 조사
            → 공백 없으면: False Positive → "이상 없음" 결론
            ※ 현재 메트릭 정상 = False Positive가 아님. 발화 시각 구간 공백 유무로만 판단
    ※ derived metric(http_server_requests_5m 등)은 raw metric(http_server_request_duration_seconds_count)
       으로부터 recording rule로 생성됨. 명명 불일치가 아님 — 혼동 금지
- Http5xxErrorRate prod thymeleaf/flask/springboot →
    metrics_agent(4xx+5xx 에러율, job="todolist/<서비스명>") + logs_agent(ERROR/SEVERE 로그) + infrastructure_agent(RDS 상태) 동시 호출
    ※ infrastructure_agent 결과가 정상 수치를 반환하면 인프라를 원인으로 지목하지 마라. 결과를 그대로 따를 것
- RDS/DB 커넥션 이상     → metrics_agent(RDS 메트릭) + logs_agent(앱 에러) 동시 호출
                           prod이면 infrastructure_agent(RDS 상태)도 동시 추가
- 응답 지연              → metrics_agent(latency·throughput) + logs_agent(slow trace) 동시 호출
- JVM / 컨테이너 이상    → metrics_agent(JVM·컨테이너) 단독 호출 (충분)
- Http4xxErrorRate / Unexpected4xxDetected →
    STEP 1: metrics_agent(4xx+5xx 비율 확인) + logs_agent(4xx 관련 ERROR 로그) 동시 호출
    STEP 2: STEP 1에서 4xx 확인되면 metrics_agent로 http_route별 드릴다운 필수
            topk(5, sum by (http_route, http_response_status_code)(...{{4xx}}[5m])) 쿼리 사용
            → 어떤 경로에서 에러가 집중되는지 특정하고 status code 분포 확인

## PHASE 2 — PHASE 1 결과가 새로운 증거를 가리킬 때만 순차 추가 호출

PHASE 1 결과를 먼저 확인하고, 그 결과가 다음 조사를 정당화할 때만 추가 호출.
- PHASE 1 결과에서 새로운 이상 징후가 발견된 경우에만 해당 증거가 가리키는 다음 계층 조사
- PHASE 1 결과가 모두 정상 → 추가 호출 없이 즉시 종료

## STOP 조건 (해당하면 즉시 종료)
- 근본 원인 + 뒷받침 증거 확보
- 모든 조사 결과 정상 → "이상 없음"이 올바른 결론
- tool 호출 4회 도달

## 시간 파싱
"어제 오후 3시" → last_minutes≈1500, "최근 30분" → 30, "지금/방금" → 10

### 한국어 절대 시각 처리 (오전/오후 N시)
- "오전/오후 N시부터", "N시 이후" 등 절대 시각 표현은 현재 KST 시각 기준으로 분 차이를 계산해 last_minutes로 변환하라.
- 예: 현재 17:30 KST, "오전 9시부터" → 17:30 − 09:00 = 510분 → last_minutes=510
- 예: 현재 10:00 KST, "오후 2시부터" → 아직 도달 안 한 시각 → 전날 14:00 기준 → last_minutes≈1200
- 절대 시각이 현재보다 미래이면 전날 동일 시각으로 처리하라.
- last_minutes=0 (instant query)으로 설정하지 마라 — 반드시 과거 시간 범위를 커버하라.

## 대상 불명확 시 처리 규칙 (역질문 금지)
- 서비스명/EC2 이름/시간 범위 등이 불명확해도 역질문하지 마라.
- 알려진 서비스 전체를 조회하거나 합리적 기본값으로 즉시 조회 시작하라.
- 조회 결과 데이터가 없으면 "해당 서비스/리소스 데이터 없음"으로 결론 내려라.
- 예) "EC2 확인해줘" → 알려진 Frontend/Backend EC2 전체 조회
- 예) "payment-service 확인해줘" → 조회 시도 후 데이터 없으면 "미등록 서비스"로 결론

## 빈 결과 처리
- 빈 결과 = 해당 시간대 이상 없음. 재시도는 1회만 허용 (시간 범위 2배 확장)
- 빈 결과를 "스크래핑 실패" 또는 "인프라 문제"로 단정 금지

## 툴 조회 실패 시 보완 조사 규칙
- metrics_agent 조회 실패 또는 데이터 없음 → logs_agent + infrastructure_agent로 보완 조사 계속하라
- logs_agent 조회 실패 → metrics_agent + infrastructure_agent로 보완 조사 계속하라
- 한 툴이 실패해도 나머지 툴로 가능한 범위까지 조사 후 결론 도출하라
- 역질문(레이블명/설정 등 추가 정보 요청) 금지 — 조회 실패 원인은 연결 문제 또는 설정 오류로 결론 내리고 다른 툴로 보완하라

## 서비스 다운 감지 규칙
- 메트릭 응답의 `_meta` 값을 반드시 확인하라
  - `_meta.no_data=true` → 조회 기간 내 데이터 전혀 없음 = 서비스 다운 강력 의심 (단순 "데이터 없음"으로 결론 내리지 마라)
  - `_meta.last_data_age_seconds > 300` (5분 초과) → 메트릭 수집 중단 = 서비스 다운 강력 의심
  - `_meta.last_data_age_seconds > 180` (3분 초과) → 이상 신호, logs_agent + infrastructure_agent로 보완 조사 필수
- 요청 수(`app_http_server_requests_5m`) 최신값이 0이거나 급감한 경우 → 단순 "정상"으로 결론 내리지 마라
- RDS `DatabaseConnections` 최신값이 0인 경우 → 앱이 DB에 연결하지 않음 = 서비스 미실행 가능성
- 위 신호 중 하나라도 감지 시 → logs_agent로 전체 로그(severity 없이) 조회하여 마지막 로그 타임스탬프 확인
  - 로그 응답에 `log_age_warning` 필드가 존재하면 이것을 반드시 결론에 포함하라
  - `log_age_warning`이 있으면 즉시 단정하지 말고 반드시 메트릭과 교차 검증하라:
    - `log_age_warning` 있음 + 메트릭 `last_data_age_seconds` < 60초 + latency/throughput 정상 → "과거 장애, 현재 자동 복구됨" 패턴 — "✅ 정상"이 아닌 "🔄 복구됨 (과거 장애 발생)" 으로 분류하라
    - `log_age_warning` 있음 + 메트릭 `_meta.no_data=true` 또는 `last_data_age_seconds` > 300초 → "현재 다운 강력 의심" 으로 분류하라
    - `log_age_warning` 있음 + 메트릭 신선도는 정상이지만 5xx 에러율 급등 중 → 서비스 실행 중이나 오류 상태, DB/의존성 문제 조사
  - "조회 범위 내"라는 이유로 로그 공백을 정상으로 합리화하지 마라 — 반드시 메트릭 신선도와 교차 검증 후 판단하라
  - 최근 5분 내 로그가 전혀 없고 메트릭도 수집 안 되면 → "서비스 다운 가능성 높음. EC2 내 컨테이너 상태 직접 확인 필요"를 결론에 명시하라
  - 최근 로그가 있으면 → 서비스 실행 중, 다른 원인 조사

- 메트릭-로그 신선도 불일치 시 (메트릭 신선 + 로그 공백) → "판단 불가"에서 멈추지 말고 반드시 다음 추가 조사를 수행하라:
  1. metrics_agent로 현재 5xx 에러율(`job:http_5xx_error_ratio:rate5m`) 최신값 조회
  2. metrics_agent로 DB 커넥션 지표(`app_db_connection_wait_p95_5m`) 조회
  3. 두 지표가 모두 정상이면 → "🔄 과거 장애, 현재 복구됨"으로 결론
  4. 5xx 에러율 또는 DB 커넥션 지표 이상이면 → "🔴 현재 진행 중 장애"로 결론

## Cross-validation
- metrics_agent가 메트릭 반환 → 서비스 정상 수집 중. "스크래핑 실패" 주장 금지
- logs_agent가 traces 반환 → 서비스 실행 중. "서비스 다운" 주장 금지
- infrastructure_agent가 RDS available → DB 서버 자체는 정상
- infrastructure_agent가 정상 수치(CPU 낮음·연결 수 정상·상태 Available)를 반환했으면 → 인프라를 원인으로 지목 금지
- 모든 조사 결과 정상 → 인프라 문제를 지어내지 말 것. "이상 없음"이 올바른 답

## CloudTrail 이벤트 인과관계 검증 규칙
- CloudTrail에서 변경 이벤트(보안그룹 변경, RDS 수정 등)를 발견해도 즉시 원인으로 단정하지 마라.
- 반드시 다음 순서로 검증하라:
  1. 알람/증상 발생 시각(state_change_time 또는 사용자 언급 시각) 확인
  2. CloudTrail 이벤트 시각이 증상 발생 시각 **이전**인지 확인 (원인은 결과보다 먼저여야 함)
  3. 해당 시간대 실제 영향(에러율 상승, latency 스파이크, 연결 실패 로그)을 메트릭/로그로 검증
  4. 위 3가지가 모두 일치할 때만 인과관계로 결론 내려라
- 이벤트 시각과 증상 시각이 불일치하거나 영향 데이터가 없으면 "인과관계 미확인"으로 명시

If the query contains a REVIEW FEEDBACK section, address each gap before concluding.

Return a concise investigation summary (max 1500 chars) with key findings. Tool이 반환한 수치는 그대로 인용할 것 — 재해석하거나 보완하지 마라."""


WRITER_PROMPT = """You are an RCA Report Writer. Write the report in Korean (한국어).
Keep technical terms in English (AMP, CloudWatch, RDS, OpenSearch, traceId, etc.).

## RAW TOOL OUTPUTS 우선 원칙 (CRITICAL)
입력에 "=== RAW TOOL OUTPUTS ===" 섹션이 있는 경우:
- 해당 섹션의 수치·상태값이 최종 권위 데이터임. Investigation summary와 수치가 다를 경우 RAW TOOL OUTPUTS의 값을 사용할 것
- Investigation summary는 인과관계 파악에만 참조하고, 수치는 반드시 RAW TOOL OUTPUTS에서 직접 인용할 것
- RAW TOOL OUTPUTS에 없는 수치를 evidence에 기재하는 것은 금지

Return ONLY valid JSON (no markdown, no explanation):
{
  "incident_summary": "1-2 sentence summary of findings",
  "likely_root_causes": ["원인 1", "원인 2"],
  "immediate_actions": ["즉시 조치 1", "즉시 조치 2"],
  "follow_up_actions": ["후속 조치 1"],
  "evidence_summary": ["근거 1 (실제 수치 포함)", "근거 2"],
  "severity": "CRITICAL or HIGH or MEDIUM or LOW",
  "confidence": "HIGH or MEDIUM or LOW",
  "evidence": {
    "metrics": "실제 조회된 수치 그대로 기재. 미조회 시 '미조회'",
    "logs": "실제 조회된 로그 건수 및 주요 내용 기재. 미조회 시 '미조회'",
    "traces": "실제 조회된 트레이스 결과 기재. 미조회 시 '미조회'",
    "infrastructure": "실제 조회된 리소스 상태 기재. 미조회 시 '미조회'"
  }
}

## NO GUESSING — EVIDENCE ONLY (STRICT)
- investigation summary에 없는 수치를 추론하거나 추측으로 채우는 것은 엄격히 금지
- 수집된 데이터에 명시적으로 존재하지 않는 내용은 원인으로 기재 불가
- tool 호출 결과 없이 "정상", "0건", "흐름 정상" 등을 단정하는 것도 금지
- tool이 error를 반환했으면 해당 내용을 evidence에 반드시 명시할 것
- EVERY CLAIM IN likely_root_causes AND evidence_summary MUST BE DIRECTLY TRACEABLE to a specific tool result

## CAUSAL CHAIN RULES
- 인과관계를 단계별로 추적: 각 단계는 독립적인 증거로 뒷받침되어야 함
- 증거 없이 중간 단계를 건너뛰는 것 금지 — 각 인과 단계마다 실제 데이터 필요

## EVIDENCE RULES
- evidence 필드는 항상 실제 조회 결과 수치로 채울 것 ("정보 없음" 금지)
- 해당 agent가 호출되지 않은 경우 → "미조회" 기재
- 에러 없으면: 정상 수치와 조회 결과를 직접 기재 (고정 문구 금지 — 실제 조사 내용 반영)
- confidence: 증거가 충분하고 인과관계 명확 → HIGH, 일부 추정 포함 → MEDIUM, 증거 부족 → LOW

## 절대 금지 — 간접 참조 표현
- "X_agent 응답 참조", "상세 결과 참조 필요", "X 결과를 참조하라" 같은 간접 표현 금지
- tool이 반환한 실제 수치·건수·에러 메시지·타임스탬프를 직접 인용할 것
- 데이터가 없으면 "0건" 또는 "해당 기간 데이터 없음"으로 명시

## 필드별 작성 규칙 (CRITICAL)

### likely_root_causes
- 수집된 증거에서 직접 도출된 원인만 기재 (고정 문구 금지 — 실제 조사 내용 반영)
- 증거로 원인을 특정할 수 없으면 원인 미특정임을 명시
- 증거 없는 원인 기재 금지
- 알람 발화 당시 에러가 0건이었음이 확인되면 [] (원인 없음)

### immediate_actions
- 현재도 장애가 진행 중인 경우에만 기재. 최대 2개. 수집된 증거에서 도출할 것 (고정 문구 금지)
- 서비스가 이미 복구됐거나 정상이면 []
- 금지: HA 구성, 자동 재시작 정책, 모니터링 추가, 로깅 강화, 연결 풀 최적화

### follow_up_actions
- 수집된 증거로 특정된 근본 원인의 재발 방지 조치만. 최대 2개. 수집된 증거에서 도출할 것 (고정 문구 금지)
- 원인이 미특정이거나 증거 기반이 없으면 []
- 금지: 예방 조치, 최적화, 일반적인 운영 개선, "정책 수립" 류

### confidence
- 원인을 증거로 직접 특정: HIGH
- 원인 추정 또는 미특정: MEDIUM
- 증거 부족: LOW

절대 금지:
- 실제 서비스 영향이 없는데 actions를 채우는 것
- 예방 조치나 모니터링 개선을 actions에 포함하는 것
- 정상 지표를 원인처럼 포장하는 것

## SEVERITY RULES
- CRITICAL: infrastructure_agent가 인프라 완전 장애(인스턴스 중지·종료·연결 불가 등)를 직접 확인,
            또는 서비스 전면 다운 (5xx 100% / 요청 처리 불가)
- HIGH    : 5xx 에러율 높음 (>10%) 또는 주요 서비스 기능 일부 불가
- MEDIUM  : 경고성 이상 (latency 상승, 간헐적 에러, 단일 컨테이너 이상) 또는 일시적 에러 자동 복구
- LOW     : 서비스 정상 (False Positive 또는 이상 없음 결론)"""


REVIEWER_PROMPT = """You are an RCA Report Quality Reviewer. Evaluate the report strictly.

## Criteria

1. ROOT CAUSE — Evidence-backed?
   - 각 원인은 investigation summary의 실제 데이터로 직접 뒷받침되어야 함
   - 수집된 데이터에 없는 내용을 원인으로 기재했으면 → FAIL
   - 모든 지표가 정상인데 인프라 원인을 지어냈으면 → FAIL

2. EVIDENCE — 실제 수치 인용?
   - 구체적인 메트릭 값, 로그 건수, 리소스 상태가 명시돼야 함
   - 수치 없이 추상적인 서술만 있으면 → FAIL

3. CAUSAL CHAIN — 각 단계에 증거가 있는가?
   - A → B → C 각 단계마다 실제 근거 필요
   - 증거 없이 단계를 건너뛰거나 추측으로 연결했으면 → FAIL
   - 잘못된 리소스에 원인을 귀속시켰으면 → FAIL

5. TIMESTAMP CAUSALITY — 시간 순서가 맞는가?
   - 알람이 시각 T에 발생했다면, 원인 후보는 반드시 T 이전에 발생한 변경/이벤트여야 함
   - T 이후에 발생한 변경을 원인으로 지목했으면 → FAIL (원인이 결과보다 나중일 수 없음)
   - CloudTrail 변경 이벤트를 원인으로 인용할 때, 그 변경 시각이 알람 발생 이전인지 반드시 확인
   - 타임스탬프 정보가 없어서 인과 순서를 검증할 수 없으면 → confidence를 LOW로 표시하고 PASS

4. ACTIONS — 근본 원인과 직접 연결되는가?
   - 알람 원인과 무관한 조치가 포함됐으면 → FAIL
   - 예방 조치나 최적화가 포함됐으면 → FAIL
   - 실제 서비스 영향 없는데 임계값 조정·모니터링 추가 등이 actions에 있으면 → FAIL
   - 정상 결론(에러 없음·서비스 정상)인데 immediate_actions 또는 follow_up_actions가 비어있지 않으면 → FAIL

## CRITICAL DISTINCTION — TWO SEPARATE METRIC SYSTEMS
- AMP/OTel metrics: application push metrics (http_server_*, jvm_*, otelcol_*)
- CloudWatch metrics: AWS infra metrics (EC2 CPU, StatusCheck, RDS, etc.)
- "OTel/AMP metric collection failure" while CloudWatch shows EC2 CPU/status = NOT a contradiction
- ServiceDown / metric-absence alerts: empty AMP results ARE the evidence → VALID report

## AUTO-FAIL CONDITIONS
- AMP 메트릭이 실제로 수집됐는데 "스크래핑 실패"를 원인으로 기재 → FAIL
- traces가 조회됐는데 "서비스 다운"을 주장 → FAIL
- prod 환경 RDS 문제인데 infrastructure_agent 증거 없음 → FAIL
- error_count=0이고 이상 없는데 인프라 원인 지어냄 → FAIL
- tool이 error 반환했는데 evidence에 미언급 → FAIL
- investigation summary에 없는 수치를 리포트에 사용 → FAIL
- 알람 발생 시각 이후에 발생한 변경을 원인으로 지목 → FAIL

## INFRA-CONFIRMED EXCEPTION (PASS 처리)

infrastructure_agent가 인프라 장애를 직접 확인한 경우, 앱 로그 부재를 이유로 FAIL 금지:
- infrastructure_agent가 RDS STOPPED / 인스턴스 terminated / 연결 불가 등을 직접 반환 →
  앱 로그에 DB 연결 에러가 없어도 인프라 증거만으로 인과관계 성립 → CAUSAL CHAIN PASS
- 이유: RDS 중지 시 앱이 이미 죽어있거나 로그가 수집되지 않을 수 있음
- 이 경우 CAUSAL CHAIN 기준 3번을 인프라 증거로 대체 가능

## VALID CONCLUSIONS (PASS 처리)
- 모든 AMP 쿼리 빈 결과 확인 → "메트릭 수집 실패"가 원인 → PASS
- 실제 쿼리로 에러 0건 확인 → "이상 없음" 결론 → PASS
- 정상 결론이라 actions=[] → PASS (조치가 없는 것이 올바른 답)
- infrastructure_agent가 RDS/인스턴스 장애 직접 확인 → 앱 로그 없어도 PASS
- 툴을 실제로 조회했음에도 메트릭/로그 데이터가 전혀 없는 경우 → "해당 이름으로 수집된 데이터 없음" 결론 → PASS
  (데이터 없음 자체가 증거임. 서비스 미등록/미배포/레이블 불일치/Collector 장애 등 가능성을 나열하는 것은 허용)
  ("추가 정보 필요" 안내 포함 여부는 FAIL 사유 아님)

Respond EXACTLY:
VERDICT: PASS or FAIL
GAPS: (only if FAIL) Actionable bullet list, max 3 items
SCORE: 1-10"""


# ============================================================
# Helpers
# ============================================================

def _parse_gaps(reviewer_text: str) -> str:
    match = re.search(r"GAPS:\s*(.*?)(?:SCORE:|$)", reviewer_text, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _is_pass(text: str) -> bool:
    upper = text.upper()
    return "VERDICT: PASS" in upper or "VERDICT:PASS" in upper


def _is_fail(text: str) -> bool:
    upper = text.upper()
    return "VERDICT: FAIL" in upper or "VERDICT:FAIL" in upper


def _extract_report_json(text: str) -> dict:
    try:
        cleaned = text.strip()
        if "```" in cleaned:
            cleaned = cleaned.split("```")[1].split("```")[0].strip()
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()
        if "{" in cleaned and "}" in cleaned:
            cleaned = cleaned[cleaned.find("{"): cleaned.rfind("}") + 1]
        return json.loads(cleaned)
    except Exception:
        return {
            "incident_summary": text[:500] if text else "분석 실패",
            "likely_root_causes": [],
            "immediate_actions": [],
            "follow_up_actions": [],
            "evidence_summary": [],
            "evidence": {},
        }


# ============================================================
# Strands Agents (모듈 레벨 싱글턴)
# ============================================================

def _collector_callback(**kwargs):
    if kwargs.get("current_tool_use"):
        tool = kwargs["current_tool_use"]
        name = tool.get("name", "")
        inp  = str(tool.get("input", ""))[:300]
        cw_log(f"[tool_call] {name} input={inp}")
    if kwargs.get("tool_result"):
        result = str(kwargs["tool_result"])[:500]
        cw_log(f"[tool_result] {result}")


def _make_collector(environment: str) -> Agent:
    """environment에 따라 tool 목록을 다르게 구성한 Collector Agent 반환.
    dev: infrastructure_agent 제외 (로컬 Docker 컨테이너 → AWS API 조회 불가)
    prod: infrastructure_agent 포함 (EC2/RDS 직접 조회)
    both: infrastructure_agent 포함 (prod 포함이므로)
    """
    tools = [metrics_agent, logs_agent]
    if environment in ("prod", "both"):
        tools.append(infrastructure_agent)
    cw_log(f"[collector] tools={[t.__name__ if hasattr(t, '__name__') else str(t) for t in tools]} (env={environment})")
    return Agent(
        name="collector",
        model=model,
        system_prompt=COLLECTOR_PROMPT,
        tools=tools,
        callback_handler=_collector_callback,
    )

_writer = Agent(
    name="writer",
    model=writer_model,  # Haiku — 구조화된 JSON 출력만 담당
    system_prompt=WRITER_PROMPT,
    tools=[],
    callback_handler=None,
)

_reviewer = Agent(
    name="reviewer",
    model=writer_model,  # Haiku — PASS/FAIL 판단만 담당
    system_prompt=REVIEWER_PROMPT,
    tools=[],
    callback_handler=None,
)


# ============================================================
# 노드 정의
# ============================================================

def lookup_memory(alert_name: str) -> tuple:
    """AgentCore Memory에서 과거 이력 조회. (stats, similar) 반환.
    SNS 도착 시점에 Lambda에서 직접 호출 가능한 공용 함수."""
    stats = {"count": 0, "is_new": True}
    similar = []
    if not alert_name:
        return stats, similar
    try:
        agentcore = AgentCoreMemory()
        if agentcore.memory_id:
            stats  = agentcore.get_stats(alert_name)
            similar = agentcore.search_similar_incidents(alert_name, limit=3)
            logger.info(f"🧠 [lookup_memory] {alert_name}: {stats.get('count', 0)}회 발생")
    except Exception as e:
        logger.warning(f"⚠️ [lookup_memory] 조회 실패: {e}")
    return stats, similar


def memory_node(state: ObservabilityState) -> ObservabilityState:
    """과거 장애 이력 조회 - AgentCore (분석 컨텍스트용)"""
    alert_name = state.get("alert_name", "")
    if not alert_name:
        return {**state, "memory_stats": {"count": 0, "is_new": True}, "similar_incidents": []}

    # SNS 수신 시 이미 조회한 결과가 있으면 재사용 (중복 조회 방지)
    if state.get("memory_stats"):
        cw_log(f"[memory_node] 🧠 캐시 사용 (재조회 생략): {alert_name}")
        return state

    cw_log(f"[memory_node] 🧠 메모리 검색: {alert_name}")
    stats, similar = lookup_memory(alert_name)

    if stats.get("is_new"):
        logger.info(f"   ⚠️ 신규 장애 패턴 (과거 이력 없음)")
    else:
        logger.info(f"   ✅ 과거 {stats['count']}회 발생")
        if stats.get("avg_resolution_time"):
            logger.info(f"   📊 평균 해결 시간: {stats['avg_resolution_time']:.1f}분")

    return {**state, "memory_stats": stats, "similar_incidents": similar}



def rca_node(state: ObservabilityState) -> ObservabilityState:
    """
    🔵 RCA 실행: Collector → Writer → Reviewer 루프 (최대 3회)
    obs-agent 스타일 품질 검증 루프
    """
    cw_log(f"[rca_node] 🔵 RCA 실행 시작: {state.get('alert_name','')}")
    logger.info("🔵 RCA 실행 중... (Collector→Writer→Reviewer)")

    alert_name        = state.get("alert_name", "")
    question          = state.get("question", "")
    stats             = state.get("memory_stats", {})
    similar           = state.get("similar_incidents", [])
    state_change_time = state.get("state_change_time", "")

    # 메모리 컨텍스트 구성
    if not stats.get("is_new"):
        memory_lines = [
            f"\n## 과거 장애 이력 (AgentCore)",
            f"- 총 {stats['count']}회 발생",
            f"- 평균 해결 시간: {stats.get('avg_resolution_time', 0):.1f}분",
            f"- 가장 흔한 원인: {stats.get('most_common_cause', 'Unknown')}",
        ]
        for idx, inc in enumerate(similar[:3], 1):
            memory_lines.append(
                f"- 사례{idx}: {inc.get('timestamp', '')} | "
                f"원인: {inc.get('root_cause', '')} | "
                f"해결: {inc.get('resolution', '')}"
            )
        memory_context = "\n".join(memory_lines)
    else:
        memory_context = "\n## 과거 장애 이력: 신규 패턴 (이력 없음)"

    # 알람 발화 시각 컨텍스트 계산
    time_context = ""
    if state_change_time:
        try:
            from datetime import timezone as _tz
            fired_at = datetime.fromisoformat(state_change_time.replace("Z", "+00:00"))
            now_utc  = datetime.now(tz=_tz.utc)
            minutes_ago = max(0, (now_utc - fired_at).total_seconds() / 60)
            query_window = int(minutes_ago + 15)  # 발화 시각 15분 전까지 커버
            time_context = (
                f"\n\n## 알람 발화 시각\n"
                f"- 발화 시각(UTC): {state_change_time}\n"
                f"- 현재로부터 약 {minutes_ago:.0f}분 전\n"
                f"- 메트릭/로그 조회 시 last_minutes={query_window} 사용 (발화 시각 ±15분 커버)\n"
                f"- 현재 상태가 아닌 발화 시각 전후 구간의 데이터를 반드시 확인할 것"
            )
        except Exception:
            pass

    base_prompt = f"{question}\n{memory_context}{time_context}"
    current_prompt = base_prompt
    last_writer_text = ""
    rca_start = time.time()

    # deployment_environment 파싱 → Collector tool 구성 결정
    env_match = re.search(r'deployment_environment[=\s"\':]+(\w+)', question)
    environment = env_match.group(1).lower() if env_match else "prod"
    _collector = _make_collector(environment)
    cw_log(f"[rca_node] environment={environment}")

    for iteration in range(1, MAX_ITERATIONS + 1):
        logger.info(f"[RCA] iteration={iteration}")

        # iteration 2+: Collector 시작 전 시간 체크
        # Collector ~120s + Writer ~10s + 여유 = 150s 필요하므로
        # 이미 150s 초과 + 결과 있으면 추가 iteration 스킵
        if iteration > 1 and last_writer_text:
            elapsed = time.time() - rca_start
            if elapsed > 150:
                logger.info(f"[RCA] 시간 예산 부족 ({elapsed:.0f}s), iteration {iteration} 스킵 → 이전 결과 사용")
                break

        # 1. Collector
        clear_raw_outputs()  # 이전 iteration tool 출력 초기화
        cw_log(f"[rca_node] Collector 호출 시작 (iteration={iteration})")
        try:
            collector_result = _collector(current_prompt)
        except Exception as e:
            cw_log(f"[rca_node] Collector 오류: {e}")
            raise
        investigation = str(collector_result)
        cw_log(f"[rca_node] Collector 완료 (iteration={iteration})\n{investigation[:1000]}")
        logger.info(f"[RCA] collector done len={len(investigation)}")
        logger.info(f"[RCA] collector summary:\n{investigation[:800]}")

        # tool 원본 반환값을 Writer 입력에 직접 추가
        # Collector가 요약 과정에서 수치를 왜곡하는 할루시네이션 방지용
        raw_outputs = get_raw_outputs()
        if raw_outputs:
            raw_section = "\n\n=== RAW TOOL OUTPUTS (AUTHORITATIVE) ===\n"
            for tool_name, output in raw_outputs.items():
                raw_section += f"[{tool_name}]:\n{output}\n\n"
            raw_section += "=== END RAW TOOL OUTPUTS ==="
            cw_log(f"[rca_node] raw_outputs captured: {list(raw_outputs.keys())}")
        else:
            raw_section = ""
            cw_log("[rca_node] raw_outputs empty (tool 미호출 또는 캡처 실패)")

        # investigation에 raw tool 데이터 병합 — Writer/Reviewer 모두 실제 수치를 받음
        # Collector의 narrative는 인과관계 파악용, 수치는 raw_section이 권위
        investigation_with_raw = investigation + raw_section

        # 2. Writer
        writer_result = _writer(investigation_with_raw)
        writer_text = str(writer_result)
        last_writer_text = writer_text
        logger.info(f"[RCA] writer done len={len(writer_text)}")

        # 시간 예산 체크 - 초과 시 강제 종료
        elapsed = time.time() - rca_start
        if elapsed > RCA_TIMEOUT_SECONDS:
            logger.info(f"[RCA] 시간 예산 초과 ({elapsed:.0f}s), 강제 종료")
            break

        # 3. Reviewer — investigation_with_raw를 전달하여 할루시네이션된 수치로 PASS 판정 방지
        reviewer_result = _reviewer(
            f"Investigation summary:\n{investigation_with_raw}\n\nReport:\n{writer_text}"
        )
        reviewer_text = str(reviewer_result)
        verdict = "PASS" if _is_pass(reviewer_text) else ("FAIL" if _is_fail(reviewer_text) else "?")
        cw_log(f"[rca_node] Reviewer verdict={verdict} (iteration={iteration})")
        logger.info(f"[RCA] reviewer verdict={verdict} iteration={iteration}")
        if verdict == "FAIL":
            logger.info(f"[RCA] reviewer full response:\n{reviewer_text}")

        if _is_pass(reviewer_text) or iteration == MAX_ITERATIONS:
            break

        gaps = _parse_gaps(reviewer_text)
        current_prompt = (
            f"{base_prompt}\n\n"
            f"--- REVIEW FEEDBACK (iteration {iteration}) ---\n"
            f"The previous analysis was rejected. Address the following gaps:\n"
            f"{gaps}\n"
            f"--- END FEEDBACK ---"
            f"{raw_section}"  # 이전 iteration raw 데이터 유지 — Collector가 재조사 시 실제 수치 참조 가능
        )
        logger.info(f"[RCA] gaps='{gaps[:200]}' → retrying collector")

    report = _extract_report_json(last_writer_text)
    session_id = f"incident-{alert_name.replace(' ', '-')}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

    cw_log(f"[rca_node] ✅ RCA 완료: {report.get('incident_summary', '')[:200]}")
    logger.info(f"✅ RCA 완료: {report.get('incident_summary', '')[:100]}")
    return {**state, "final_answer": json.dumps(report, ensure_ascii=False), "session_id": session_id}


def memory_save_node(state: ObservabilityState) -> ObservabilityState:
    """DynamoDB에 ongoing 임시 저장 (AgentCore 장기 메모리는 조치완료 시에만 저장)"""
    alert_name        = state.get("alert_name", "")
    session_id        = state.get("session_id", "")
    state_change_time = state.get("state_change_time", datetime.utcnow().isoformat())
    memory_stats      = state.get("memory_stats", {})

    try:
        report = json.loads(state.get("final_answer", "{}"))
    except Exception:
        report = {}

    # Writer가 판정한 severity 우선 사용, 없으면 알람 원본 severity 사용
    severity = report.get("severity", state.get("severity", "high")).lower()

    # 이미 ongoing 인시던트 존재 시 스킵 (DynamoDB 기준)
    existing = get_ongoing_incident(alert_name)
    if existing:
        print(f"⏭️ DynamoDB 저장 스킵 - 이미 ongoing 인시던트 존재")
        return state

    incident_data = {
        "alert_name": alert_name,
        "severity": severity,
        "root_cause": ", ".join(report.get("likely_root_causes", [])),
        "resolution": "ongoing",
        "resolution_time": 0,
        "metrics": {
            "session_id": session_id,
            "is_recurring": not memory_stats.get("is_new", True),
            "past_occurrences": memory_stats.get("count", 0),
        },
        "error_messages": report.get("evidence_summary", []),
        "state_change_time": state_change_time,
        "slack_ts":      state.get("slack_ts", ""),
        "slack_channel": state.get("slack_channel", ""),
        "status": "ongoing",
    }

    # DynamoDB에만 임시 저장 (AgentCore는 조치완료 시 resolved 레코드로 저장)
    put_ongoing_incident(alert_name, incident_data)
    logger.info(f"✅ DynamoDB ongoing 저장 완료 (AgentCore 장기 메모리는 조치완료 시 저장)")

    return state


# ============================================================
# Graph 구성 (단순화: memory → rca → memory_save)
# ============================================================

def build_graph():
    graph = StateGraph(ObservabilityState)

    graph.add_node("memory",      memory_node)
    graph.add_node("rca",         rca_node)
    graph.add_node("memory_save", memory_save_node)

    graph.set_entry_point("memory")
    graph.add_edge("memory",      "rca")
    graph.add_edge("rca",         "memory_save")
    graph.add_edge("memory_save", END)

    return graph.compile()


# ============================================================
# 장애 해결 후 메모리 저장
# ============================================================

def save_resolution(alert_name: str, actual_resolution: str, resolution_time_minutes: float):
    """
    장애 해결 후 호출 (Slack 모달 제출 시).

    Args:
        alert_name:              알람 이름
        actual_resolution:       사용자가 입력한 실제 조치 내용
        resolution_time_minutes: 탐지부터 해결까지 소요 시간(분)
    """
    from dynamodb_incident import delete_ongoing_incident, get_ongoing_incident

    # DynamoDB에서 root_cause / severity 가져오기 (삭제 전에)
    ongoing = get_ongoing_incident(alert_name)
    root_cause = ongoing.get('root_cause', 'Unknown') if ongoing else 'Unknown'
    severity   = ongoing.get('severity', 'medium')    if ongoing else 'medium'

    try:
        AgentCoreMemory().save_incident({
            "alert_name":       alert_name,
            "root_cause":       root_cause,
            "resolution":       actual_resolution or "조치 내용 미입력",
            "resolution_time":  resolution_time_minutes,
            "severity":         severity,
            "status":           "resolved",
        })
    except Exception as e:
        print(f"⚠️ [AgentCore] 해결 이력 저장 실패: {e}")

    delete_ongoing_incident(alert_name)
    print(f"✅ 장애 해결 이력 저장 완료")


# ============================================================
# 로컬 테스트
# ============================================================
if __name__ == "__main__":
    app = build_graph()
    result = app.invoke({
        "question": "JVM 메모리가 높습니다",
        "alert_name": "HighJvmMemory",
        "severity": "high",
        "amp_link": "",
        "category": [],
        "memory_stats": {},
        "similar_incidents": [],
        "final_answer": "",
        "session_id": "",
        "state_change_time": datetime.utcnow().isoformat(),
        "slack_ts": "",
        "slack_channel": "",
    })
    report = json.loads(result["final_answer"])
    print(f"\n🤖 요약: {report.get('incident_summary')}")
    print(f"   즉시 조치: {report.get('immediate_actions')}")
