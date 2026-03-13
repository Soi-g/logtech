바로 실무에 붙일 수 있게 초안 형태로 한 번에 정리해둘게.
이번에는 1) Supervisor 프롬프트, 2) Action Group JSON 스펙, 3) Lambda/API 함수 목록, 4) 수동 테스트 예시까지 이어서 쓸게.

Thought for 9s

좋아. 아래는 바로 MVP로 시작할 수 있게 만든 초안 패키지야.

1) Supervisor 프롬프트 초안

아래 프롬프트는 수동 요청 기반 observability 분석 supervisor 용도야.
지금 단계에서는 workflow형 orchestration을 전제로 하고, tool 호출은 제한된 action만 쓰게 설계했다.

You are an observability incident analysis supervisor.

Your role:
- Analyze service issues using telemetry, historical summaries, and runbook knowledge.
- Coordinate available actions in a controlled workflow.
- Produce evidence-based RCA and recommended actions.

Primary goal:
Given a user request about a service issue, collect relevant evidence from:
1. metrics
2. logs
3. traces
4. runbook knowledge
5. historical analytics (optional)

Then produce:
- incident summary
- likely root cause
- evidence
- confidence
- recommended actions
- missing data / next checks

Operating rules:
- Never claim a root cause without evidence.
- Prefer recent telemetry evidence in the requested time range.
- Use metrics first to identify symptom patterns.
- Use logs to find concrete error signals and repeated patterns.
- Use traces to identify slow spans, failing dependencies, and latency concentration.
- Use runbook results only as operational guidance, not as direct proof.
- Use historical analysis only for comparison or anomaly context.
- If evidence is insufficient or conflicting, say so explicitly.
- Do not invent missing values.
- Do not generate raw OpenSearch DSL, raw SQL, or unsupported queries.
- Use only the provided actions.

Workflow:
1. Parse user request:
   - service
   - environment
   - time_range
   - symptom(s)
   - whether historical comparison is needed
2. Call metrics summary action first.
3. If needed, call logs search.
4. If needed, call traces search.
5. If available, call runbook search.
6. If requested or useful, call historical context.
7. Synthesize findings into the final structured response.

Analysis policy:
- Distinguish clearly between observed evidence and inferred conclusions.
- When multiple root cause hypotheses exist, rank them.
- Prefer the simplest explanation supported by evidence.
- Mention uncertainty.
- Keep all analysis scoped to the requested service and environment.

Output format:
Return JSON with the following fields:
{
  "status": "ok|insufficient_data|error",
  "incident_summary": "...",
  "time_range": {
    "from": "...",
    "to": "..."
  },
  "service": "...",
  "environment": "...",
  "symptoms": [],
  "likely_root_cause": {
    "summary": "...",
    "confidence": "low|medium|high",
    "hypotheses": [
      {
        "rank": 1,
        "title": "...",
        "confidence": "low|medium|high",
        "reason": "..."
      }
    ]
  },
  "evidence": {
    "metrics": [],
    "logs": [],
    "traces": [],
    "runbook": [],
    "historical": []
  },
  "recommended_actions": [],
  "missing_data": [],
  "next_checks": []
}

Style rules:
- Be concise but specific.
- Prefer operational language.
- Include concrete metric/log/trace clues when available.
- Do not over-explain basic observability concepts.
2) Action Group 입력/출력 JSON 스펙 초안

핵심 원칙은 이거야.

자유 질의 금지

자유 SQL 금지

자유 OpenSearch DSL 금지

서비스/환경/시간 범위 중심

응답은 항상 정형 JSON

LLM이 바로 synthesis 가능하도록 summary, evidence, results 구조 제공

2-1) 공통 요청 메타 스키마

각 action에 공통으로 들어가면 좋은 필드:

{
  "service": "springboot",
  "environment": "dev",
  "time_range": {
    "type": "relative",
    "last_minutes": 15
  },
  "limit": 20,
  "request_id": "optional-correlation-id"
}

또는 절대 시간도 허용:

{
  "service": "springboot",
  "environment": "dev",
  "time_range": {
    "type": "absolute",
    "from": "2026-03-11T15:00:00Z",
    "to": "2026-03-11T15:15:00Z"
  }
}
2-2) get_metrics_summary
input
{
  "service": "springboot",
  "environment": "dev",
  "time_range": {
    "type": "relative",
    "last_minutes": 15
  },
  "metrics": [
    "request_count",
    "error_count",
    "error_ratio",
    "latency_p95",
    "container_cpu_pct_avg",
    "container_memory_pct_avg",
    "jvm_cpu_pct_avg",
    "jvm_memory_used_bytes_avg"
  ]
}
output
{
  "status": "ok",
  "summary": {
    "request_count": 1832,
    "error_count": 221,
    "error_ratio": 0.1206,
    "latency_p95_ms": 1840,
    "container_cpu_pct_avg": 81.3,
    "container_memory_pct_avg": 72.5,
    "jvm_cpu_pct_avg": 68.2,
    "jvm_memory_used_bytes_avg": 734003200
  },
  "baseline_comparison": {
    "previous_equal_window": {
      "latency_p95_ms": 320,
      "error_ratio": 0.014
    },
    "changes": [
      "latency_p95 increased significantly versus previous window",
      "error_ratio increased significantly versus previous window"
    ]
  },
  "evidence": [
    "High latency observed in the last 15 minutes",
    "Error ratio elevated above recent baseline"
  ],
  "results": [
    {
      "metric": "latency_p95",
      "unit": "ms",
      "value": 1840
    },
    {
      "metric": "error_ratio",
      "unit": "ratio",
      "value": 0.1206
    }
  ]
}
2-3) search_logs
input
{
  "service": "springboot",
  "environment": "dev",
  "time_range": {
    "type": "relative",
    "last_minutes": 15
  },
  "symptom_keywords": [
    "timeout",
    "exception",
    "error",
    "connection refused"
  ],
  "sources": [
    "app"
  ],
  "limit": 20
}
output
{
  "status": "ok",
  "summary": "Repeated timeout and exception patterns found in springboot application logs.",
  "pattern_counts": [
    {
      "pattern": "timeout",
      "count": 12
    },
    {
      "pattern": "SQLTransientConnectionException",
      "count": 7
    }
  ],
  "top_findings": [
    "Timeout-related errors repeatedly appeared during latency spike window",
    "DB connection acquisition failures were found"
  ],
  "evidence": [
    "12 timeout-related log entries detected",
    "7 DB connection-related exceptions detected"
  ],
  "results": [
    {
      "@timestamp": "2026-03-11T15:08:22Z",
      "service_name": "springboot",
      "severity_text": "ERROR",
      "message": "Timeout while waiting for DB response",
      "trace_id": "abc123",
      "span_id": "def456"
    }
  ]
}
2-4) search_traces
input
{
  "service": "springboot",
  "environment": "dev",
  "time_range": {
    "type": "relative",
    "last_minutes": 15
  },
  "trace_filters": [
    "high_latency",
    "error"
  ],
  "limit": 20
}
output
{
  "status": "ok",
  "summary": "Slow traces show latency concentration on downstream DB spans.",
  "top_findings": [
    "DB spans dominate end-to-end latency in slowest traces",
    "A subset of traces includes timeout errors"
  ],
  "evidence": [
    "Top slow traces are dominated by db span duration",
    "Multiple failed traces share the same downstream dependency pattern"
  ],
  "results": [
    {
      "trace_id": "abc123",
      "root_service": "springboot",
      "duration_ms": 2310,
      "error": true,
      "dominant_span": {
        "name": "SELECT todo_items",
        "service": "mysql",
        "duration_ms": 1840
      }
    }
  ]
}
2-5) get_historical_context

처음에는 optional 또는 stub 가능.

input
{
  "service": "springboot",
  "environment": "dev",
  "lookback_days": 7,
  "symptoms": [
    "high_latency",
    "high_error_rate"
  ]
}
output
{
  "status": "ok",
  "summary": "Similar latency spikes occurred twice in the last 7 days.",
  "top_findings": [
    "Similar incident on 2026-03-08 correlated with elevated DB latency",
    "Current latency is higher than 7-day median"
  ],
  "evidence": [
    "2 similar latency spike windows found in last 7 days"
  ],
  "results": [
    {
      "incident_time": "2026-03-08T03:15:00Z",
      "latency_p95_ms": 1320,
      "error_ratio": 0.07,
      "likely_pattern": "db_slowdown"
    }
  ]
}
2-6) search_runbook

지금은 runbook이 없으니 stub부터.

input
{
  "service": "springboot",
  "environment": "dev",
  "symptoms": [
    "high_latency",
    "high_error_rate"
  ],
  "keywords": [
    "timeout",
    "database",
    "connection pool"
  ]
}
output
{
  "status": "not_available",
  "summary": "No runbook available yet for the requested service/symptom.",
  "top_findings": [],
  "evidence": [],
  "results": []
}

나중에 RAG 붙이면:

{
  "status": "ok",
  "summary": "Relevant runbook snippets found for springboot DB timeout handling.",
  "top_findings": [
    "Check DB connection pool saturation",
    "Inspect slow queries and downstream DB latency"
  ],
  "evidence": [
    "Runbook section matched timeout + DB latency scenario"
  ],
  "results": [
    {
      "document_id": "rb-001",
      "title": "Springboot DB Timeout Troubleshooting",
      "snippet": "Check connection pool exhaustion and downstream DB response time..."
    }
  ]
}
3) 최종 Supervisor 응답 JSON 스키마 초안

이건 action 응답들을 합쳐서 최종적으로 LLM이 내보낼 형식이야.

{
  "status": "ok",
  "incident_summary": "springboot 서비스 최근 15분 동안 latency와 error ratio가 상승했습니다.",
  "time_range": {
    "from": "2026-03-11T15:00:00Z",
    "to": "2026-03-11T15:15:00Z"
  },
  "service": "springboot",
  "environment": "dev",
  "symptoms": [
    "high_latency",
    "high_error_rate"
  ],
  "likely_root_cause": {
    "summary": "DB 응답 지연 및 connection pool 관련 병목 가능성이 가장 높습니다.",
    "confidence": "medium",
    "hypotheses": [
      {
        "rank": 1,
        "title": "DB 응답 지연 또는 connection pool 병목",
        "confidence": "medium",
        "reason": "latency p95 증가, timeout 로그 반복, slow trace의 dominant span이 DB 호출에 집중됨"
      },
      {
        "rank": 2,
        "title": "애플리케이션 CPU 부하 증가",
        "confidence": "low",
        "reason": "container/jvm cpu 상승은 있으나 직접적인 오류 패턴은 DB 관련 신호가 더 강함"
      }
    ]
  },
  "evidence": {
    "metrics": [
      "latency_p95 1840ms로 상승",
      "error_ratio 12.06%로 상승",
      "container_cpu_pct_avg 81.3%"
    ],
    "logs": [
      "timeout 관련 로그 12건",
      "DB connection 관련 예외 7건"
    ],
    "traces": [
      "느린 trace에서 DB span이 대부분의 지연 차지"
    ],
    "runbook": [],
    "historical": []
  },
  "recommended_actions": [
    "DB 응답시간 및 slow query 확인",
    "connection pool 포화 여부 확인",
    "최근 배포/설정 변경 여부 확인",
    "CPU 부하 지속 시 애플리케이션 스케일 조정 검토"
  ],
  "missing_data": [
    "DB 자체 메트릭 부재",
    "배포 변경 이력 미연결"
  ],
  "next_checks": [
    "DB latency 메트릭 추가 연동",
    "recent deployment metadata 연동"
  ]
}
4) Lambda / API 함수 목록 초안

네 MVP 기준으로는 Lambda가 제일 무난해 보여.
처음엔 전부 Lambda로 두고, 나중에 필요하면 일부는 별도 API/서비스로 분리하면 된다.

4-1) 추천 함수 목록
1. get_metrics_summary

역할:

AMP에서 PromQL 실행

최근 N분 메트릭 요약

이전 equal window 비교

결과를 정형 JSON으로 반환

입력:

service

environment

time_range

metrics list

출력:

summary

baseline_comparison

evidence

2. search_logs

역할:

AOS에서 logs-app 또는 logs-host 조회

서비스/환경/시간범위 필터

symptom keyword 패턴 집계

representative hits 반환

입력:

service

environment

time_range

symptom_keywords

sources

limit

출력:

summary

pattern_counts

top_findings

evidence

results

3. search_traces

역할:

traces-app 조회 또는 현재 trace 저장소 조회

느린 trace / error trace 추출

dominant span 분석

dependency latency 요약

입력:

service

environment

time_range

trace_filters

limit

출력:

summary

top_findings

evidence

results

4. get_historical_context

역할:

Athena에서 curated metric table 조회

최근 7일/30일 유사 패턴 비교

이상 패턴이나 반복 incident 요약

입력:

service

environment

lookback_days

symptoms

출력:

summary

top_findings

evidence

results

5. search_runbook

역할:

지금은 stub

나중엔 S3 + Bedrock KB/RAG 검색

입력:

service

environment

symptoms

keywords

출력:

summary

top_findings

evidence

results

4-2) Lambda 내부 구현 팁
get_metrics_summary

내부에서 service별로 허용 metric만 매핑하는 게 좋다.

예:

request_count -> app_http_server_requests_5m

error_ratio -> app_http_server_error_ratio_5m

latency_p95 -> app_http_server_latency_p95_5m

container_cpu_pct_avg -> app_container_cpu_utilization_avg_5m

jvm_cpu_pct_avg -> app_jvm_cpu_utilization_pct_avg_5m

즉 에이전트가 metric 이름을 마음대로 쓰게 하지 말고, alias → 실제 PromQL 매핑 구조로 가는 게 좋다.

search_logs

AOS 인덱스는 우선:

logs-app-*

logs-host-*

필터는 최소:

service.name

deployment.environment

@timestamp

source

로그 본문 전체를 에이전트에게 많이 주지 말고:

pattern_counts

top sample 3~5개

trace_id 연결 가능 여부
위주로 요약해서 넘겨.

search_traces

나중에 AOS trace 인덱스로 갈 거면:

traces-app-*
기준으로 맞춰두고,
현재 Jaeger 확인 단계여도 Lambda 응답 스키마는 동일하게 유지해.

중요한 건 backend가 바뀌어도 supervisor prompt/action spec을 안 바꾸는 것.

5) 수동 테스트용 입력 예시

아래 같은 요청이 바로 MVP 테스트 질문이 된다.

예시 1
{
  "service": "springboot",
  "environment": "dev",
  "time_range": {
    "type": "relative",
    "last_minutes": 15
  },
  "question": "최근 15분 원인 분석해줘",
  "symptoms": [
    "high_latency"
  ],
  "include_historical": false
}
예시 2
{
  "service": "thymeleaf",
  "environment": "dev",
  "time_range": {
    "type": "relative",
    "last_minutes": 15
  },
  "question": "latency가 높은 이유와 조치안 알려줘",
  "symptoms": [
    "high_latency"
  ],
  "include_historical": true
}
예시 3
{
  "service": "flask",
  "environment": "dev",
  "time_range": {
    "type": "relative",
    "last_minutes": 15
  },
  "question": "에러율이 오른 이유 알려줘",
  "symptoms": [
    "high_error_rate"
  ],
  "include_historical": false
}
6) MVP workflow 초안

지금 네 상황에서 가장 현실적인 workflow는 이거야.

Step 1

Supervisor가 입력에서:

service

env

symptom

time range
추출

Step 2

get_metrics_summary 호출
증상 존재 확인

Step 3

증상에 따라 분기

high_latency면 logs + traces 둘 다 조회

high_error_rate면 logs 우선, traces 보조

resource issue 의심되면 metrics만으로도 1차 결론 가능

Step 4

search_runbook 호출
지금은 stub

Step 5

include_historical=true일 때만 get_historical_context

Step 6

최종 synthesis

summary

likely root cause

evidence

actions

missing_data

7) 지금 바로 구현 우선순위

정말 바로 시작하려면 이 순서 추천.

우선 1

get_metrics_summary Lambda부터
이게 제일 중요함.
왜냐하면 supervisor가 맨 처음 증상 유무를 판단하는 근거가 되기 때문.

우선 2

search_logs Lambda
error/timeout 패턴만 잘 뽑아도 체감 성과가 큼.

우선 3

search_traces Lambda
현재 traces는 Jaeger 확인 중이어도 응답 스키마만 먼저 맞춰도 됨.

우선 4

Supervisor prompt 연결

우선 5

runbook stub

우선 6

historical stub 또는 Athena 연결

8) 내가 보기엔 이렇게 시작하면 된다

가장 작은 첫 성공 기준:

Bedrock Supervisor 1개

action 3개

metrics

logs

traces

입력 질문 1개

“springboot 서비스 최근 15분 원인 분석해줘”

출력 JSON + 한국어 자연어 요약

이 정도면 충분히 “AI 부분 시작했다”라고 볼 수 있어.

다음 단계로 바로 이어서
Lambda 기준으로 get_metrics_summary / search_logs / search_traces 함수 인터페이스를 Python 코드 골격으로 써줄 수 있어.