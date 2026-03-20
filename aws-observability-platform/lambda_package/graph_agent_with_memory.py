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
import os
import re
import time
from datetime import datetime
from typing import Annotated, TypedDict

from langgraph.graph import END, StateGraph
from strands import Agent
from strands.models import BedrockModel

from agents_aws import infrastructure_agent, logs_agent, metrics_agent
from agentcore_memory import AgentCoreMemory
from dynamodb_incident import get_ongoing_incident, put_ongoing_incident

MODEL_ID       = os.environ.get("ANALYSIS_MODEL_ID", "apac.anthropic.claude-sonnet-4-20250514-v1:0")
AWS_REGION     = os.environ.get("AWS_REGION_NAME", "ap-northeast-2")
MAX_ITERATIONS = 2
RCA_TIMEOUT_SECONDS = 200  # Lambda 300s timeout 기준 여유 확보

model = BedrockModel(model_id=MODEL_ID, region_name=AWS_REGION, streaming=False)


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

COLLECTOR_PROMPT = """You are an SRE Information Collector for an observability platform.

Your job: gather data about the incident from the user's query. Focus on the CAUSAL CHAIN.

You have 3 specialist agents as tools:
- metrics_agent       : AMP(OTel) + CloudWatch 메트릭 조회 (앱/JVM/컨테이너/RDS/EC2)
- logs_agent          : OpenSearch 로그 + 트레이스 조회 + CloudWatch Logs
- infrastructure_agent : AWS RDS/EC2 현재 상태 직접 조회 (boto3)

## 각 데이터 소스 호출 기준

metrics_agent → 거의 항상 호출 (기준 데이터: HTTP 에러율, latency, CPU, JVM 등)
logs_agent    → 에러/예외/서비스다운/크래시 의심 시 호출 (ERROR/SEVERE 로그, 에러 트레이스)
               **logs_agent는 앱 로그(logs-app)만 봅니다. 인프라/OS 로그가 나오면 무시하세요.**
infrastructure_agent → prod 환경 + RDS/EC2/인프라 관련 증상 의심 시만 호출

## 환경 구분
- "dev"  : VM/Docker 로컬 환경 (기본값)
- "prod" : EC2/AWS/고객 환경 언급 시

## 조사 전략

STEP 1 — 증상 분류:
- App 에러 (5xx, error rate, 서비스 다운) → STEP 2
- 응답 지연 (latency, 느림) → metrics_agent + logs_agent(traces 포함)
- JVM (GC, heap, thread) → metrics_agent로 JVM 메트릭 집중 확인
- RDS/DB (커넥션, 쿼리 지연) → metrics_agent → prod면 infrastructure_agent도 호출
- 인프라 (CPU, 메모리, 컨테이너) → metrics_agent로 컨테이너/호스트 메트릭 확인

STEP 2 — App layer:
  metrics_agent: HTTP 에러율, latency, 요청 수 확인
  logs_agent:    ERROR/SEVERE 로그 확인
  → 에러 발견 + 원인 불명확 → STEP 3

STEP 3 — 드릴다운 (필요 시):
  metrics_agent: container CPU/memory, JVM heap/GC, DB 커넥션 풀
  logs_agent:    host syslog OOM/network 에러 확인
  → prod 환경이면 infrastructure_agent로 EC2/RDS 상태 확인

STEP 4 — RDS/DB layer (prod 환경에서 DB 관련 원인 의심 시):
  metrics_agent:        prod RDS CloudWatch 커넥션 수, CPU, ReadLatency 확인
  infrastructure_agent: prod RDS 현재 상태, 이벤트 이력 확인

STEP 5 — 종료:
  - 원인 특정 + 근거 확보 → 요약 후 종료
  - 모든 관련 단계 완료 → 요약 후 종료

## 시간 파싱
"어제 오후 3시" → last_minutes≈1500, "최근 30분" → 30, "지금/방금" → 10

## 빈 결과 처리
- 빈 결과 반환 시 → 더 넓은 시간 범위로 재시도 (10분 → 30분 → 60분)
- 빈 결과 ≠ 스크래핑 실패 또는 인프라 문제
- AMP 파생 메트릭은 5분 집계 지연 가능 → 빈 경우 raw 메트릭 시도

## Cross-validation (중요)
- metrics_agent가 메트릭 반환 → 서비스 정상 수집 중. "스크래핑 실패" 주장 금지
- logs_agent가 traces 반환 → 서비스 실행 중. "서비스 다운" 주장 금지
- infrastructure_agent가 RDS available → DB 서버 자체는 정상

If the query contains a REVIEW FEEDBACK section, address each gap before concluding.

Return a concise investigation summary (max 1500 chars) with key findings and actual numbers."""


WRITER_PROMPT = """You are an RCA Report Writer. Write the report in Korean (한국어).
Keep technical terms in English (AMP, CloudWatch, RDS, OpenSearch, traceId, etc.).

Return ONLY valid JSON (no markdown, no explanation):
{
  "incident_summary": "1-2 sentence summary of findings",
  "likely_root_causes": ["원인 1", "원인 2"],
  "immediate_actions": ["즉시 조치 1", "즉시 조치 2"],
  "follow_up_actions": ["후속 조치 1"],
  "evidence_summary": ["근거 1 (실제 수치 포함)", "근거 2"],
  "evidence": {
    "metrics": "실제 수치 기재 (예: error_ratio=0.08, RDS CPU=85%, connections=98/100)",
    "logs": "조회 결과 기재 (예: ERROR 3건, SEVERE 1건, 주요 메시지 요약)",
    "traces": "조회 결과 기재 또는 '미조회'",
    "infrastructure": "AWS 리소스 상태 (예: RDS available, EC2 running) 또는 '미조회'"
  }
}

EVIDENCE RULES:
- evidence 필드는 항상 실제 조회 결과 수치로 채울 것 ("정보 없음" 금지)
- 해당 agent가 호출되지 않은 경우 → "미조회" 기재
- 에러 없으면: "조회 기간 동안 에러가 발생하지 않았습니다" + 정상 수치 기재
- 정상 결론 시: likely_root_causes=[], immediate_actions=[], follow_up_actions=[]

DATA INTEGRITY RULES (STRICT):
- investigation summary에 없는 수치를 추론하거나 추측으로 채우는 것은 엄격히 금지
- tool 호출 결과 없이 "정상", "0건", "흐름 정상" 등을 단정하는 것도 금지
- tool이 error를 반환했으면 해당 내용을 evidence에 명시할 것

CRITICAL:
- EVERY CLAIM MUST BE BACKED BY EVIDENCE
- immediate_actions: 근본 원인을 직접 해결하는 최소한의 조치만 (예방 조치, 최적화 제외)"""


REVIEWER_PROMPT = """You are an RCA Report Quality Reviewer. Evaluate the report.

Criteria:
1. ROOT CAUSE: Specific, evidence-backed?
2. EVIDENCE: Concrete metric values, log entries, trace data cited?
3. CAUSAL CHAIN: Each step has evidence? No assumed steps?
4. ACTIONS: Direct causal link? No unnecessary fixes?

CRITICAL DISTINCTION — TWO SEPARATE METRIC SYSTEMS:
- AMP/OTel metrics: application push metrics (http_server_*, jvm_*, otelcol_*)
- CloudWatch metrics: AWS infra metrics (EC2 CPU, StatusCheck, RDS, etc.)
- "OTel/AMP metric collection failure" while CloudWatch shows EC2 CPU/status = NOT a contradiction
- ServiceDown / metric-absence alerts: empty AMP results ARE the evidence. Report that accurately reflects "all AMP queries returned empty" is VALID.

CONTRADICTION CHECK (auto-FAIL):
- Claims AMP/OTel scraping failure but AMP metrics (http_*, jvm_*, etc.) were actually retrieved → FAIL
- Claims service unavailable but traces show active spans → FAIL
- Claims RDS problem without infrastructure_agent evidence (prod env) → FAIL
- Invents root causes when error_count=0 and no anomaly → FAIL
- tool이 error를 반환했는데 evidence에 해당 실패가 언급되지 않으면 → FAIL

NO-ERROR / NO-DATA ACCEPTANCE:
- All AMP queries confirmed empty across multiple time ranges → root cause = metric collection failure → PASS
- Zero errors confirmed by actual queries → PASS
- Do NOT fail a valid "no incident" or "metric absence confirmed" report

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

_collector = Agent(
    name="collector",
    model=model,
    system_prompt=COLLECTOR_PROMPT,
    tools=[metrics_agent, logs_agent, infrastructure_agent],
    callback_handler=None,
)

_writer = Agent(
    name="writer",
    model=model,
    system_prompt=WRITER_PROMPT,
    tools=[],
    callback_handler=None,
)

_reviewer = Agent(
    name="reviewer",
    model=model,
    system_prompt=REVIEWER_PROMPT,
    tools=[],
    callback_handler=None,
)


# ============================================================
# 노드 정의
# ============================================================

def memory_node(state: ObservabilityState) -> ObservabilityState:
    """과거 장애 이력 조회 - AgentCore"""
    alert_name = state.get("alert_name", "")
    if not alert_name:
        return {**state, "memory_stats": {"count": 0, "is_new": True}, "similar_incidents": []}

    print(f"🧠 메모리 검색: {alert_name}")
    stats = None
    similar = []

    agentcore = AgentCoreMemory()
    if agentcore.memory_id:
        try:
            stats = agentcore.get_stats(alert_name)
            similar = agentcore.search_similar_incidents(alert_name, limit=3)
            print(f"   📡 소스: AgentCore")
        except Exception as e:
            print(f"   ⚠️ [AgentCore] 조회 실패: {e}")
            stats = None

    if stats is None:
        stats = {"count": 0, "is_new": True}
        similar = []

    if stats.get("is_new"):
        print(f"   ⚠️ 신규 장애 패턴 (과거 이력 없음)")
    else:
        print(f"   ✅ 과거 {stats['count']}회 발생")
        if stats.get("avg_resolution_time"):
            print(f"   📊 평균 해결 시간: {stats['avg_resolution_time']:.1f}분")
        if stats.get("most_common_cause"):
            print(f"   🔍 가장 흔한 원인: {stats['most_common_cause']}")

    return {**state, "memory_stats": stats, "similar_incidents": similar}


def rca_node(state: ObservabilityState) -> ObservabilityState:
    """
    🔵 RCA 실행: Collector → Writer → Reviewer 루프 (최대 3회)
    obs-agent 스타일 품질 검증 루프
    """
    print("🔵 RCA 실행 중... (Collector→Writer→Reviewer)")

    alert_name = state.get("alert_name", "")
    question   = state.get("question", "")
    stats      = state.get("memory_stats", {})
    similar    = state.get("similar_incidents", [])

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

    base_prompt = f"{question}\n{memory_context}"
    current_prompt = base_prompt
    last_writer_text = ""
    rca_start = time.time()

    for iteration in range(1, MAX_ITERATIONS + 1):
        print(f"[RCA] iteration={iteration}")

        # iteration 2+: Collector 시작 전 시간 체크
        # Collector ~120s + Writer ~10s + 여유 = 150s 필요하므로
        # 이미 150s 초과 + 결과 있으면 추가 iteration 스킵
        if iteration > 1 and last_writer_text:
            elapsed = time.time() - rca_start
            if elapsed > 150:
                print(f"[RCA] 시간 예산 부족 ({elapsed:.0f}s), iteration {iteration} 스킵 → 이전 결과 사용")
                break

        # 1. Collector
        collector_result = _collector(current_prompt)
        investigation = str(collector_result)
        print(f"[RCA] collector done len={len(investigation)}")
        print(f"[RCA] collector summary:\n{investigation[:800]}")

        # 2. Writer
        writer_result = _writer(investigation)
        writer_text = str(writer_result)
        last_writer_text = writer_text
        print(f"[RCA] writer done len={len(writer_text)}")

        # 시간 예산 체크 - 초과 시 강제 종료
        elapsed = time.time() - rca_start
        if elapsed > RCA_TIMEOUT_SECONDS:
            print(f"[RCA] 시간 예산 초과 ({elapsed:.0f}s), 강제 종료")
            break

        # 3. Reviewer
        reviewer_result = _reviewer(
            f"Investigation summary:\n{investigation}\n\nReport:\n{writer_text}"
        )
        reviewer_text = str(reviewer_result)
        verdict = "PASS" if _is_pass(reviewer_text) else ("FAIL" if _is_fail(reviewer_text) else "?")
        print(f"[RCA] reviewer verdict={verdict} iteration={iteration}")

        if _is_pass(reviewer_text) or iteration == MAX_ITERATIONS:
            break

        gaps = _parse_gaps(reviewer_text)
        current_prompt = (
            f"{base_prompt}\n\n"
            f"--- REVIEW FEEDBACK (iteration {iteration}) ---\n"
            f"The previous analysis was rejected. Address the following gaps:\n"
            f"{gaps}\n"
            f"--- END FEEDBACK ---"
        )
        print(f"[RCA] gaps='{gaps[:200]}' → retrying collector")

    report = _extract_report_json(last_writer_text)
    session_id = f"incident-{alert_name.replace(' ', '-')}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

    print(f"✅ RCA 완료: {report.get('incident_summary', '')[:100]}")
    return {**state, "final_answer": json.dumps(report, ensure_ascii=False), "session_id": session_id}


def memory_save_node(state: ObservabilityState) -> ObservabilityState:
    """장기 메모리 저장 (RCA 이후)"""
    alert_name        = state.get("alert_name", "")
    severity          = state.get("severity", "high")
    session_id        = state.get("session_id", "")
    state_change_time = state.get("state_change_time", datetime.utcnow().isoformat())
    memory_stats      = state.get("memory_stats", {})

    try:
        report = json.loads(state.get("final_answer", "{}"))
    except Exception:
        report = {}

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

    # 이미 ongoing 인시던트 존재 시 스킵 (DynamoDB 기준)
    existing = get_ongoing_incident(alert_name)
    if existing:
        print(f"⏭️ 메모리 저장 스킵 - 이미 ongoing 인시던트 존재")
        return state

    # AgentCore 저장 → record ID DynamoDB에 함께 보관
    agentcore_record_id = ''
    try:
        agentcore_record_id = AgentCoreMemory().save_incident(incident_data) or ''
    except Exception as e:
        print(f"⚠️ [AgentCore] 저장 실패: {e}")

    # DynamoDB ongoing 저장 (agentcore_record_id 포함)
    incident_data['agentcore_record_id'] = agentcore_record_id
    put_ongoing_incident(alert_name, incident_data)
    print(f"✅ 장기 메모리 저장 완료 (status: ongoing)")

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
