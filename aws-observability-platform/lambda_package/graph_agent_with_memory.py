"""
AWS Observability Platform - Graph Agent with Memory
기존 LangGraph 구조 유지 + DynamoDB 장기 메모리 추가
"""

import os
import json
from typing import TypedDict, Annotated
from datetime import datetime

from langgraph.graph import StateGraph, END
from langchain_aws import ChatBedrockConverse

from agents_aws import (
    get_metrics_summary, get_jvm_metrics, get_http_metrics,
    get_logs_summary, search_logs, get_error_logs,
    get_traces_summary, get_slow_spans, get_trace_by_id,
    metrics_agent, logs_agent, traces_agent
)
from analysis_agents import (
    metrics_analysis_agent,
    logs_analysis_agent,
    traces_analysis_agent,
    analysis_agent
)
from runbooks_aws import search_runbook
from strands import Agent
from strands.models import BedrockModel

# Bedrock Agent Runtime 추가
import boto3
bedrock_agent_runtime = boto3.client('bedrock-agent-runtime', region_name='ap-northeast-2')

AGENT_ID = os.environ.get('BEDROCK_AGENT_ID')
AGENT_ALIAS_ID = os.environ.get('BEDROCK_AGENT_ALIAS_ID')

# IncidentMemory 임포트 (AOSS - fallback)
from incident_memory import IncidentMemory

# AgentCore Memory 임포트 (Phase 2: primary)
from agentcore_memory import AgentCoreMemory


# ============================================================
# State 정의 (메모리 필드 추가)
# ============================================================
def merge_str(existing: str, new: str) -> str:
    return new if new else existing

class ObservabilityState(TypedDict):
    question:        Annotated[str, lambda x, y: y]
    alert_name:      Annotated[str, lambda x, y: y]
    severity:        Annotated[str, lambda x, y: y]
    amp_link:        Annotated[str, lambda x, y: y]
    category:        Annotated[list[str], lambda x, y: y]
    
    # 메모리 관련 필드
    memory_stats:    Annotated[dict, lambda x, y: y]
    similar_incidents: Annotated[list, lambda x, y: y]
    
    # Collection 결과 필드
    collection_result: Annotated[str, merge_str]  # 🆕 Collection Agent 결과
    
    # Analysis 결과 필드
    metrics_analysis:  Annotated[str, merge_str]  # 🆕 메트릭 분석
    logs_analysis:     Annotated[str, merge_str]  # 🆕 로그 분석
    traces_analysis:   Annotated[str, merge_str]  # 🆕 트레이스 분석
    analysis_result:   Annotated[str, merge_str]  # 🆕 종합 분석
    
    # 기타
    runbook_result:  Annotated[str, merge_str]
    
    # 최종 결과
    session_id:        Annotated[str, lambda x, y: y]  # 🆕 Bedrock Agent 세션
    state_change_time: Annotated[str, lambda x, y: y]  # 🆕 Phase 2: SNS 이벤트 시각
    final_answer:      Annotated[str, lambda x, y: y]


# ============================================================
# LLM 설정 (모델 계층화)
# ============================================================

# 🔵 Haiku 3.5 - 분류 작업 (비용 효율적)
classify_llm = ChatBedrockConverse(
    model="apac.anthropic.claude-sonnet-4-20250514-v1:0",
    region_name="ap-northeast-2",
)

# 🟢 Sonnet 3.5 v2 - Collection/Analysis Main Agent (고품질)
sonnet_model = BedrockModel(
    model_id="apac.anthropic.claude-sonnet-4-20250514-v1:0",
    region_name="ap-northeast-2",
    streaming=False
)


# ============================================================
# 노드 정의 (메모리 노드 추가)
# ============================================================

def memory_node(state: ObservabilityState) -> ObservabilityState:
    """
    Phase 2: 과거 장애 이력 조회 - AgentCore 우선, AOSS 폴백
    """
    alert_name = state.get("alert_name", "")

    if not alert_name:
        return {
            **state,
            "memory_stats": {'count': 0, 'is_new': True},
            "similar_incidents": []
        }

    print(f"🧠 메모리 검색: {alert_name}")

    stats = None
    similar = []
    source = "agentcore"

    # AgentCore 우선 조회
    agentcore = AgentCoreMemory()
    if agentcore.memory_id:
        try:
            stats = agentcore.get_stats(alert_name)
            similar = agentcore.search_similar_incidents(alert_name, limit=3)
            print(f"   📡 소스: AgentCore")
        except Exception as e:
            print(f"   ⚠️ [AgentCore] 조회 실패, AOSS 폴백: {e}")
            stats = None

    # AgentCore 미설정 또는 실패 시 AOSS 폴백
    if stats is None:
        source = "aoss"
        try:
            aoss = IncidentMemory()
            stats = aoss.get_stats(alert_name)
            similar = aoss.search_similar_incidents(alert_name, limit=3)
            print(f"   📡 소스: AOSS (폴백)")
        except Exception as e:
            print(f"   ⚠️ [AOSS] 조회도 실패: {e}")
            stats = {'count': 0, 'is_new': True}
            similar = []

    if stats.get('is_new'):
        print(f"   ⚠️ 신규 장애 패턴 (과거 이력 없음) [{source}]")
    else:
        print(f"   ✅ 과거 {stats['count']}회 발생 [{source}]")
        if stats.get('avg_resolution_time'):
            print(f"   📊 평균 해결 시간: {stats['avg_resolution_time']:.1f}분")
        if stats.get('most_common_cause'):
            print(f"   🔍 가장 흔한 원인: {stats['most_common_cause']}")

    return {
        **state,
        "memory_stats": stats,
        "similar_incidents": similar
    }


def collection_agent_node(state: ObservabilityState) -> ObservabilityState:
    """
    🔵 Collection Agent (Agent as Tools)
    Main Agent가 Sub-agents를 Tool로 사용하여 데이터 수집
    """
    print("🔵 Collection Agent 실행 중...")
    
    stats = state.get("memory_stats", {})
    alert_name = state.get("alert_name", "")
    question = state.get("question", "")
    
    # Collection Agent 생성
    collection_agent = Agent(
        model=sonnet_model,
        tools=[metrics_agent, logs_agent, traces_agent],
        system_prompt=f"""
당신은 데이터 수집 전문가입니다.

알람: {alert_name}
과거 이력: {stats.get('count', 0)}회 발생, 평균 {stats.get('avg_resolution_time', 0):.1f}분 소요

역할:
1. 알람 분석에 필요한 데이터 수집
2. 어떤 sub-agent를 호출할지 동적 판단
3. 중간 결과를 보고 추가 조사 결정
4. 수집한 원시 데이터를 정리하여 반환

사용 가능한 sub-agents:
- metrics_agent: 메트릭 분석 (JVM, CPU, 메모리, HTTP)
- logs_agent: 로그 분석 (에러, WARN, GC 로그)
- traces_agent: 트레이스 분석 (느린 요청, 서비스 간 호출)

**중요**: 분석하지 말고 데이터만 수집하세요.

출력 형식:
[수집된 데이터]

메트릭:
- JVM 메모리: 95%
- Old Gen: 98%

로그:
- OutOfMemoryError (10:23:45)
- Full GC 실패 (회수율 2%)

트레이스:
- 느린 요청 없음
"""
    )
    
    # Collection Agent 실행
    result = collection_agent(question)
    
    # 디버그: metrics_agent 결과 로깅
    result_str = str(result)
    print(f"[DEBUG] Collection 결과 ({len(result_str)}자):")
    print(result_str[:3000])
    
    print(f"✅ Collection Agent 완료")
    return {**state, "collection_result": result_str}


def runbook_node(state: ObservabilityState) -> ObservabilityState:
    print("📖 런북 검색 중...")
    try:
        query = f"{state.get('alert_name', '')} {state['question']}"
        runbooks = search_runbook(query, n_results=2)

        if not runbooks:
            return {**state, "runbook_result": "관련 런북 없음"}

        lines = []
        for rb in runbooks:
            lines.append(f"[{rb['source']}] (관련도: {rb['relevance']:.2f})")
            lines.append(f"  섹션: {rb['section']}")
            if rb.get("content"):
                lines.append(f"  내용: {rb['content'][:300]}")

        result = "\n".join(lines)
        return {**state, "runbook_result": result}

    except Exception as e:
        print(f"런북 검색 오류: {e}")
        return {**state, "runbook_result": f"런북 검색 실패: {e}"}


def analysis_agent_node(state: ObservabilityState) -> ObservabilityState:
    """
    🟡 Analysis Agent (Agent as Tools)
    Main Agent가 Sub-agents를 Tool로 사용하여 데이터 분석
    """
    print("🟡 Analysis Agent 실행 중...")
    
    stats = state.get("memory_stats", {})
    alert_name = state.get("alert_name", "")
    collection_result = state.get("collection_result", "")
    runbook_result = state.get("runbook_result", "")
    
    # 과거 이력 정보
    memory_info = ""
    if not stats.get('is_new'):
        similar = state.get("similar_incidents", [])
        memory_info = f"""
과거 이력:
- 총 {stats['count']}회 발생
- 평균 해결 시간: {stats['avg_resolution_time']:.1f}분
- 가장 흔한 원인: {stats['most_common_cause']}

최근 3건:"""
        for idx, incident in enumerate(similar[:3], 1):
            memory_info += f"""
{idx}. {incident.get('timestamp', 'Unknown')}
   원인: {incident.get('root_cause', 'Unknown')}
   해결: {incident.get('resolution', 'Unknown')}
   소요: {incident.get('resolution_time_minutes', 0)}분"""
    else:
        memory_info = "⚠️ 신규 장애 패턴 (과거 이력 없음)"
    
    # Analysis Agent 실행
    result = analysis_agent(f"""
알람: {alert_name}

{memory_info}

수집된 데이터:
{collection_result}

런북:
{runbook_result}

위 정보를 바탕으로 종합 분석하세요.
""")
    
    print(f"✅ Analysis Agent 완료")
    return {**state, "analysis_result": str(result)}


def report_agent_node(state: ObservabilityState) -> ObservabilityState:
    """
    🟢 Report Agent (Bedrock Agent)
    최종 JSON 보고서 작성
    - Session Memory 활용
    - Knowledge Base 추가 참조
    """
    print("🟢 Report Agent 실행 중...")
    
    alert_name = state.get("alert_name", "Unknown")
    severity = state.get("severity", "critical")
    stats = state.get("memory_stats", {})
    collection_result = state.get("collection_result", "")
    analysis_result = state.get("analysis_result", "")
    runbook_result = state.get("runbook_result", "")
    
    # Bedrock Agent에 전달할 컨텍스트
    context = f"""
🚨 알람 발생
알람명: {alert_name}
심각도: {severity}

📊 과거 이력:
- 발생 횟수: {stats.get('count', 0)}회
- 평균 해결 시간: {stats.get('avg_resolution_time', 0):.1f}분
- 가장 흔한 원인: {stats.get('most_common_cause', 'Unknown')}

📋 수집된 데이터:
{collection_result}

🔍 분석 결과:
{analysis_result}

📖 런북:
{runbook_result}

위 정보를 바탕으로 아래 JSON 형식으로 최종 보고서를 작성하세요.

{{
  "incident_summary": "한 문장 요약",
  "is_recurring": {not stats.get('is_new')},
  "past_occurrences": {stats.get('count', 0)},
  "likely_root_causes": ["원인1", "원인2"],
  "severity": "{severity}",
  "impact": "영향 범위",
  "immediate_actions": ["즉시 조치1", "즉시 조치2"],
  "follow_up_actions": ["후속 조치1"],
  "evidence_summary": ["근거1", "근거2"],
  "runbook_references": [{{"source": "파일명", "section": "섹션", "relevance": "관련성"}}]
}}
"""
    
    # Session ID 생성 (공백 제거)
    session_id = f"incident-{alert_name.replace(' ', '-')}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    
    try:
        # Bedrock Agent Runtime 호출
        response = bedrock_agent_runtime.invoke_agent(
            agentId=AGENT_ID,
            agentAliasId=AGENT_ALIAS_ID,
            sessionId=session_id,
            enableTrace=True,
            inputText=context
        )
        
        # 응답 스트림 처리
        result_text = ""
        for event in response['completion']:
            if 'chunk' in event:
                chunk = event['chunk']
                if 'bytes' in chunk:
                    result_text += chunk['bytes'].decode('utf-8')
        
        print(f"✅ Bedrock Agent 분석 완료 ({len(result_text)} 글자)")
        
        # JSON 파싱
        try:
            cleaned_text = result_text.strip()
            if '```json' in cleaned_text:
                cleaned_text = cleaned_text.split('```json')[1].split('```')[0].strip()
            elif '```' in cleaned_text:
                cleaned_text = cleaned_text.split('```')[1].split('```')[0].strip()
            if '{' in cleaned_text and '}' in cleaned_text:
                start_idx = cleaned_text.find('{')
                end_idx = cleaned_text.rfind('}') + 1
                cleaned_text = cleaned_text[start_idx:end_idx]
            parsed = json.loads(cleaned_text)
            
            # runbook_references가 비어있으면 runbook_result에서 직접 주입
            if not parsed.get("runbook_references") and runbook_result and runbook_result != "관련 런북 없음":
                refs = []
                seen = set()
                for line in runbook_result.split("\n"):
                    if line.startswith("[") and "]" in line:
                        filename = line.split("[")[1].split("]")[0]
                        if filename not in seen:
                            seen.add(filename)
                            refs.append({"title": filename, "summary": filename.replace(".md", "") + " 런북 참조"})
                parsed["runbook_references"] = refs
                print(f"📚 runbook_references 직접 주입: {len(refs)}건")
            
            final = json.dumps(parsed, ensure_ascii=False)
            
        except json.JSONDecodeError as e:
            print(f"⚠️ JSON 파싱 실패: {e}")
            stats = state.get("memory_stats", {})
            fallback = {
                "incident_summary": f"{alert_name} 알람 발생",
                "is_recurring": not stats.get('is_new'),
                "past_occurrences": stats.get('count', 0),
                "likely_root_causes": [stats.get('most_common_cause', '분석 필요')],
                "severity": severity,
                "impact": "영향 범위 파악 중",
                "immediate_actions": ["시스템 로그 확인"],
                "follow_up_actions": ["근본 원인 분석"],
                "evidence_summary": [],
                "runbook_references": [],
                "raw_response": result_text[:500]
            }
            final = json.dumps(fallback, ensure_ascii=False)
        
        return {**state, "final_answer": final, "session_id": session_id}
        
    except Exception as e:
        print(f"❌ Bedrock Agent 호출 실패: {e}")
        stats = state.get("memory_stats", {})
        fallback = {
            "incident_summary": f"{alert_name} 알람 - Agent 호출 실패",
            "is_recurring": not stats.get('is_new'),
            "past_occurrences": stats.get('count', 0),
            "likely_root_causes": ["Agent 호출 실패"],
            "severity": severity,
            "impact": "분석 불가",
            "immediate_actions": ["수동 확인 필요"],
            "follow_up_actions": ["Agent 설정 점검"],
            "evidence_summary": [f"오류: {str(e)}"],
            "runbook_references": []
        }
        final = json.dumps(fallback, ensure_ascii=False)
        return {**state, "final_answer": final}


def memory_save_node(state: ObservabilityState) -> ObservabilityState:
    """
    Phase 2: 장기 메모리 저장 (report_agent 이후)
    AgentCore 저장 (primary) + AOSS 저장 (secondary)
    이미 ongoing 인시던트가 있으면 스킵
    """
    alert_name = state.get("alert_name", "")
    severity = state.get("severity", "high")
    session_id = state.get("session_id", "")
    final_answer = state.get("final_answer", "{}")
    state_change_time = state.get("state_change_time", datetime.utcnow().isoformat())
    memory_stats = state.get("memory_stats", {})

    # final_answer에서 report 파싱
    try:
        report = json.loads(final_answer)
    except Exception:
        report = {}

    incident_data = {
        'alert_name': alert_name,
        'severity': severity,
        'root_cause': ', '.join(report.get('likely_root_causes', [])),
        'resolution': 'ongoing',
        'resolution_time': 0,
        'metrics': {
            'session_id': session_id,
            'is_recurring': report.get('is_recurring', not memory_stats.get('is_new', True)),
            'past_occurrences': report.get('past_occurrences', memory_stats.get('count', 0)),
        },
        'error_messages': report.get('evidence_summary', []),
        'state_change_time': state_change_time,
        'status': 'ongoing',
    }

    # 이미 ongoing 인시던트 존재 시 스킵 (AOSS 기준으로 체크)
    try:
        aoss = IncidentMemory()
        existing = aoss.get_recent_ongoing_incident(alert_name)
        if existing:
            print(f"⏭️ 장기 메모리 저장 스킵 - 이미 ongoing 인시던트 존재 ({existing.get('incident_id', '')})")
            return state
    except Exception as e:
        print(f"⚠️ [ongoing 체크 실패]: {e}")
        aoss = None

    # AgentCore 저장 (primary)
    try:
        AgentCoreMemory().save_incident(incident_data)
    except Exception as e:
        print(f"⚠️ [AgentCore] 저장 실패: {e}")

    # AOSS 저장 (secondary)
    try:
        if aoss is None:
            aoss = IncidentMemory()
        aoss.save_incident(incident_data)
        print(f"✅ 장기 메모리 저장 완료 (status: ongoing)")
    except Exception as e:
        print(f"⚠️ [AOSS] 저장 실패: {e}")

    return state


# ============================================================
# Graph 구성 (Bedrock Agent 노드 추가)
# ============================================================
def build_graph():
    """
    완전한 Agent as Tools 아키텍처
    """
    graph = StateGraph(ObservabilityState)

    # 노드 정의
    graph.add_node("memory", memory_node)
    graph.add_node("collection_agent", collection_agent_node)  # 🔵 Agent as Tools
    graph.add_node("runbook", runbook_node)
    graph.add_node("analysis_agent", analysis_agent_node)      # 🟡 Agent as Tools
    graph.add_node("report_agent", report_agent_node)          # 🟢 Bedrock Agent
    graph.add_node("memory_save", memory_save_node)            # 🆕 Phase 2: 메모리 저장

    # 흐름: memory → collection → runbook → analysis → report → memory_save
    graph.set_entry_point("memory")
    graph.add_edge("memory", "collection_agent")
    graph.add_edge("collection_agent", "runbook")
    graph.add_edge("runbook", "analysis_agent")
    graph.add_edge("analysis_agent", "report_agent")
    graph.add_edge("report_agent", "memory_save")              # 🆕 Phase 2
    graph.add_edge("memory_save", END)

    return graph.compile()


# ============================================================
# 장애 해결 후 메모리 저장 함수
# ============================================================
def save_resolution(alert_name: str, report: dict, resolution_time_minutes: int):
    """
    장애 해결 후 호출 (Slack에서 "해결 완료" 버튼 클릭 시)
    """
    memory = IncidentMemory()
    
    incident_data = {
        'alert_name': alert_name,
        'root_cause': report.get('likely_root_causes', ['Unknown'])[0],
        'resolution': ', '.join(report.get('immediate_actions', [])),
        'resolution_time': resolution_time_minutes,
        'severity': report.get('severity', 'medium'),
        'metrics': {}
    }
    
    memory.save_incident(incident_data)
    print(f"✅ 장애 해결 이력 저장 완료")


if __name__ == "__main__":
    print("=" * 60)
    print("🔍 AWS Observability Platform - Graph Agent with Memory")
    print("=" * 60)

    app = build_graph()

    result = app.invoke({
        "question": "JVM 메모리가 높습니다",
        "alert_name": "HighJvmMemory",
        "severity": "high",
        "amp_link": "",
        "category": [],
        "memory_stats": {},
        "similar_incidents": [],
        "collection_result": "",
        "metrics_analysis": "",
        "logs_analysis": "",
        "traces_analysis": "",
        "analysis_result": "",
        "runbook_result": "",
        "session_id": "",
        "state_change_time": datetime.utcnow().isoformat(),
        "final_answer": "",
    })

    report = json.loads(result["final_answer"])
    print(f"\n🤖 요약: {report.get('incident_summary')}")
    print(f"   재발 여부: {'✅ 과거 {0}회 발생'.format(report.get('past_occurrences')) if report.get('is_recurring') else '⚠️ 신규 패턴'}")
    print(f"   즉시 조치: {report.get('immediate_actions')}\n")