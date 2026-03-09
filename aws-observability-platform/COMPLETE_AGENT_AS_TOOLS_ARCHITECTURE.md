# 완전한 Agent as Tools 아키텍처

## 개요

이 시스템은 **계층적 Agent as Tools** 패턴을 사용합니다:
- Collection Agent: Sub-agents를 Tool로 사용 (데이터 수집)
- Analysis Agent: Sub-agents를 Tool로 사용 (데이터 분석)
- Report Agent: Bedrock Agent (최종 보고서)

---

## 전체 구조

```
┌─────────────────────────────────────────────────────────────┐
│ LangGraph (워크플로우 제어)                                  │
│                                                             │
│  memory_node (AOSS 과거 이력)                               │
│    ↓                                                        │
│  ┌───────────────────────────────────────────────────────┐ │
│  │ Collection Agent (Agent as Tools) 🔵                  │ │
│  │                                                       │ │
│  │  Main: Collection Agent (Sonnet 3.5 v2)              │ │
│  │    ├─ Tool: metrics_agent (Haiku 3.5)                │ │
│  │    │   └─ Tools: get_metrics_summary,                │ │
│  │    │             get_jvm_metrics,                     │ │
│  │    │             get_http_metrics                     │ │
│  │    ├─ Tool: logs_agent (Haiku 3.5)                   │ │
│  │    │   └─ Tools: get_logs_summary,                   │ │
│  │    │             search_logs,                         │ │
│  │    │             get_error_logs                       │ │
│  │    └─ Tool: traces_agent (Haiku 3.5)                 │ │
│  │        └─ Tools: get_traces_summary,                 │ │
│  │                  get_slow_spans,                      │ │
│  │                  get_trace_by_id                      │ │
│  └───────────────────────────────────────────────────────┘ │
│    ↓                                                        │
│  runbook_node (Knowledge Base 검색)                         │
│    ↓                                                        │
│  ┌───────────────────────────────────────────────────────┐ │
│  │ Analysis Agent (Agent as Tools) 🟡                    │ │
│  │                                                       │ │
│  │  Main: Analysis Agent (Sonnet 3.5 v2)                │ │
│  │    ├─ Tool: metrics_analysis_agent (Haiku 3.5)       │ │
│  │    │   └─ 메트릭 데이터 깊이 분석                    │ │
│  │    ├─ Tool: logs_analysis_agent (Haiku 3.5)          │ │
│  │    │   └─ 로그 패턴 분석                             │ │
│  │    └─ Tool: traces_analysis_agent (Haiku 3.5)        │ │
│  │        └─ 트레이스 흐름 분석                         │ │
│  └───────────────────────────────────────────────────────┘ │
│    ↓                                                        │
│  ┌───────────────────────────────────────────────────────┐ │
│  │ Report Agent (Bedrock Agent) 🟢                       │ │
│  │                                                       │ │
│  │  Bedrock Agent Runtime (Sonnet 3.5 v2)               │ │
│  │    ├─ Session Memory (30일)                          │ │
│  │    └─ Knowledge Base 연동                            │ │
│  └───────────────────────────────────────────────────────┘ │
│    ↓                                                        │
│  Slack 알림                                                 │
└─────────────────────────────────────────────────────────────┘
```

---

## 계층 구조

### Level 1: Collection (데이터 수집)

```
Collection Agent (Main)
  ├─ metrics_agent (Sub-agent)
  │   ├─ get_metrics_summary (Tool)
  │   ├─ get_jvm_metrics (Tool)
  │   └─ get_http_metrics (Tool)
  │
  ├─ logs_agent (Sub-agent)
  │   ├─ get_logs_summary (Tool)
  │   ├─ search_logs (Tool)
  │   └─ get_error_logs (Tool)
  │
  └─ traces_agent (Sub-agent)
      ├─ get_traces_summary (Tool)
      ├─ get_slow_spans (Tool)
      └─ get_trace_by_id (Tool)
```

**역할**: 필요한 데이터 수집
**모델**: Main (Sonnet 3.5 v2), Sub-agents (Haiku 3.5)

---

### Level 2: Analysis (데이터 분석)

```
Analysis Agent (Main)
  ├─ metrics_analysis_agent (Sub-agent)
  │   └─ 메트릭 데이터 깊이 분석
  │       - 정상 범위 비교
  │       - 이상 패턴 식별
  │       - 심각도 평가
  │
  ├─ logs_analysis_agent (Sub-agent)
  │   └─ 로그 패턴 분석
  │       - 에러 심각도 평가
  │       - 에러 연관성 파악
  │       - 타임라인 재구성
  │
  └─ traces_analysis_agent (Sub-agent)
      └─ 트레이스 흐름 분석
          - 병목 지점 식별
          - 서비스 간 의존성
          - 에러 전파 경로
```

**역할**: 도메인별 깊이 분석 + 종합 판단
**모델**: Main (Sonnet 3.5 v2), Sub-agents (Haiku 3.5)

---

### Level 3: Report (보고서 작성)

```
Report Agent (Bedrock Agent)
  ├─ Session Memory (30일)
  └─ Knowledge Base (런북)
```

**역할**: 최종 JSON 보고서 작성
**모델**: Sonnet 3.5 v2

---

## 실행 흐름 예시

### 시나리오: HighJvmMemory 알람

#### 1. Collection Agent

```python
collection_agent:
  "JVM 메모리 알람이네? 메트릭부터 확인하자"
  
  → metrics_agent 호출
    metrics_agent:
      "JVM 메트릭을 봐야겠다"
      → get_jvm_metrics() 호출
      → "JVM 메모리 95%, Old Gen 98%"
  
  "메모리가 높네? 로그도 확인해야겠다"
  
  → logs_agent 호출
    logs_agent:
      "에러 로그를 찾아보자"
      → get_error_logs() 호출
      → "OutOfMemoryError 발견"
  
  "OOM이 있네? GC 로그도 봐야겠다"
  
  → logs_agent 다시 호출
    logs_agent:
      "GC 로그를 검색하자"
      → search_logs("GC") 호출
      → "Full GC 실패, 회수율 2%"
  
  "트레이스는 필요 없을 것 같다"
  
  결과: """
  메트릭:
  - JVM 메모리: 95%
  - Old Gen: 98%
  
  로그:
  - OutOfMemoryError (10:23:45)
  - Full GC 실패 (회수율 2%)
  """
```

---

#### 2. Analysis Agent

```python
analysis_agent:
  "수집된 데이터를 분석하자"
  
  → metrics_analysis_agent 호출
    metrics_analysis_agent:
      "Old Gen 98%는 심각한 수준"
      "Full GC 실패 가능성 높음"
      "심각도: Critical"
  
  → logs_analysis_agent 호출
    logs_analysis_agent:
      "OutOfMemoryError 주기적 발생 (10분 간격)"
      "Old Gen 영역 관련"
      "Full GC 직후 발생"
  
  → traces_analysis_agent 호출
    traces_analysis_agent:
      "트레이스 상 이상 없음"
      "외부 API 문제 아님"
      "애플리케이션 내부 문제"
  
  "종합하면..."
  
  결과: """
  근본 원인:
  - Old Gen 메모리 누수
  
  도메인 간 연관성:
  - 메트릭: Old Gen 98% (메모리 누수 신호)
  - 로그: OOM (메모리 부족 확인)
  - 트레이스: 정상 (외부 요인 배제)
  → 애플리케이션 내부 메모리 누수 확실
  
  심각도: Critical
  증거: Full GC 회수율 2%, OOM 주기적 발생
  """
```

---

#### 3. Report Agent

```python
report_agent:
  input: """
  과거 이력: 5회 발생, 평균 45분 소요
  수집 데이터: JVM 95%, Old Gen 98%, OOM
  분석 결과: Old Gen 메모리 누수, Critical
  런북: HighJvmMemory.md
  """
  
  # Bedrock Agent가 Session Memory + Knowledge Base 활용
  
  output: {
    "incident_summary": "Old Gen 영역 메모리 누수로 인한 Full GC 실패",
    "likely_root_causes": ["Old Gen 메모리 누수", "캐시 정책 오류"],
    "severity": "critical",
    "immediate_actions": ["힙 덤프 수집", "애플리케이션 재시작"],
    "runbook_references": ["HighJvmMemory.md"]
  }
```

---

## 비용 분석

### 알람당 LLM 호출

| Agent | 모델 | 호출 횟수 | 입력 토큰 | 출력 토큰 | 비용 |
|-------|------|----------|----------|----------|------|
| Collection Agent (Main) | Sonnet 3.5 v2 | 1 | 1,000 | 200 | $0.006 |
| metrics_agent | Haiku 3.5 | 1 | 1,000 | 500 | $0.0028 |
| logs_agent | Haiku 3.5 | 2 | 2,000 | 1,000 | $0.0056 |
| traces_agent | Haiku 3.5 | 0 | 0 | 0 | $0 |
| Analysis Agent (Main) | Sonnet 3.5 v2 | 1 | 3,000 | 500 | $0.0165 |
| metrics_analysis | Haiku 3.5 | 1 | 1,000 | 500 | $0.0028 |
| logs_analysis | Haiku 3.5 | 1 | 1,500 | 600 | $0.0036 |
| traces_analysis | Haiku 3.5 | 1 | 800 | 400 | $0.0024 |
| Report Agent | Sonnet 3.5 v2 | 1 | 6,000 | 1,000 | $0.033 |

**총 비용**: $0.073/알람

**월간 비용** (1,000 알람): $73

---

## 장점

### 1. 확장성

```python
# 새로운 도메인 추가 쉬움
collection_agent = Agent(
    tools=[
        metrics_agent,
        logs_agent,
        traces_agent,
        db_agent,        # 🆕 DB 분석
        cache_agent,     # 🆕 캐시 분석
        network_agent    # 🆕 네트워크 분석
    ]
)

analysis_agent = Agent(
    tools=[
        metrics_analysis_agent,
        logs_analysis_agent,
        traces_analysis_agent,
        db_analysis_agent,      # 🆕
        cache_analysis_agent,   # 🆕
        network_analysis_agent  # 🆕
    ]
)
```

---

### 2. 전문성

각 Sub-agent가 도메인 전문가:
- metrics_analysis_agent: 메트릭 정상 범위, 임계값 전문
- logs_analysis_agent: 로그 패턴, 에러 분류 전문
- traces_analysis_agent: 서비스 의존성, 병목 분석 전문

---

### 3. 병렬 처리

```python
# LangGraph에서 병렬 실행 가능
graph.add_edge("collection", "metrics_analysis")  # 동시
graph.add_edge("collection", "logs_analysis")     # 동시
graph.add_edge("collection", "traces_analysis")   # 동시
```

---

### 4. 디버깅

각 단계 출력 확인:
```python
print("수집 결과:", state['collection_result'])
print("메트릭 분석:", state['metrics_analysis'])
print("로그 분석:", state['logs_analysis'])
print("트레이스 분석:", state['traces_analysis'])
print("종합 분석:", state['analysis_result'])
print("최종 보고서:", state['final_answer'])
```

---

## 파일 구조

```
lambda_package/
├─ agents_aws.py              # Collection Sub-agents
│   ├─ metrics_agent
│   ├─ logs_agent
│   └─ traces_agent
│
├─ analysis_agents.py         # Analysis Sub-agents 🆕
│   ├─ metrics_analysis_agent
│   ├─ logs_analysis_agent
│   ├─ traces_analysis_agent
│   └─ analysis_agent (Main)
│
└─ graph_agent_with_memory.py # LangGraph 통합
    ├─ memory_node
    ├─ collection_agent_node
    ├─ runbook_node
    ├─ analysis_agent_node
    └─ report_agent_node
```

---

## 다음 단계

1. `graph_agent_with_memory.py`에 analysis_agent_node 추가
2. State에 analysis 관련 필드 추가
3. 테스트 및 디버깅
4. 비용 모니터링

---

**작성일**: 2026-03-09  
**버전**: 2.0  
**상태**: Analysis Agent as Tools 추가 완료
