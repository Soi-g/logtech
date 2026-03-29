# 프로젝트 구조

담당자: 이찬휘
상태: In Progress

### **1. 데이터 수집**

애플리케이션 → Customer OTel Collector → Platform OTel → AMP / OpenSearch / S3

OTel은 2개 구성

- **OTel Collector Agent**
    - telemetry 수집 후 Cognito JWT를 발급해 Platform으로 전송
- Platform OTel
    - Envoy가 JWT를 검증한 뒤 otelcol-contrib으로 전달, 신호 유형별로 분리해 저장소로 라우팅

<aside>

**수집하는 데이터**

- app
logs / metrics / traces
- container
metrics
- host 
logs / metrics
</aside>

<aside>

### :opensearch: **OpenSearch에 저장되는 Logs 형식**

- OTLP JSON 형식
- `logs-app` / `logs-host` 인덱스에 분리 저장
- ISM 정책으로 30일 후 자동 삭제

| 필드 | 설명 |
| --- | --- |
| `@timestamp` | 로그 발생 시각 |
| `body` | 로그 메시지 본문 |
| `severity.text` | 로그 레벨 (INFO / WARN / ERROR / SEVERE) |
| `resource.service.name` | 서비스명 (springboot, flask 등) |
| `resource.deployment.environment` | 환경 (dev / prod) |
| `traceId`, `spanId` | traces-app 인덱스와 연계 키 |
| `host.name` | 호스트 로그의 경우 호스트명 |

> Spring Boot는 ERROR 대신 SEVERE를 사용
> 
</aside>

<aside>

### :opensearch: **OpenSearch에 저장되는 Traces 형식**

- OTLP JSON 형식
- `traces-app` 인덱스에 저장
- ISM 정책으로 30일 후 자동 삭제

| 필드 | 설명 |
| --- | --- |
| `traceId` | 전체 요청을 묶는 트레이스 ID |
| `spanId`, `parentSpanId` | span 계층 구조 |
| `name` | span 이름 (엔드포인트, 메서드명 등) |
| `kind` | span 유형 (Server / Client / Internal) |
| `startTime` | span 시작 시각 (시간 필터 기준) |
| `status.code` | Unset (정상) / Ok (명시적 성공) / Error |
| `resource.service.name` | 서비스명 |
</aside>

<aside>

### :amp: **AMP에 저장되는 Metrics 형식**

OTel Gateway에서 Remote Write로 전송되며, recording rule이 파생 메트릭을 생성(interval 30s, alert용 1m)

공통 라벨: `job`, `service_name`, `service_namespace`, `deployment_environment`, `source`

- **App HTTP** (`source=app`)
    
    
    | 파생 지표 | 설명 |
    | --- | --- |
    | `app_http_server_requests_5m` | 5분간 전체 요청 수 |
    | `app_http_server_5xx_errors_5m` | 5분간 5xx 에러 수 |
    | `app_http_server_4xx_errors_5m` | 5분간 4xx 에러 수 |
    | `app_http_server_5xx_error_ratio_5m` | 5xx 에러율 (5xx / 전체) |
    | `app_http_server_4xx_error_ratio_5m` | 4xx 에러율 (4xx / 전체) |
    | `app_http_server_latency_p95_5m` | 응답 지연 P95 |
- **Container** (`source=container`, springboot·thymeleaf·flask 대상)
    
    
    | 파생 지표 | 설명 |
    | --- | --- |
    | `app_container_cpu_utilization_avg_5m` | 컨테이너 CPU 사용률 평균 |
    | `app_container_cpu_utilization_max_5m` | 컨테이너 CPU 사용률 최대 |
    | `app_container_memory_utilization_avg_5m` | 컨테이너 메모리 사용률 평균 |
    | `app_container_memory_utilization_max_5m` | 컨테이너 메모리 사용률 최대 |
- **JVM** (`source=app`, Spring Boot 전용)
    
    
    | 파생 지표 | 설명 |
    | --- | --- |
    | `app_jvm_cpu_utilization_avg_5m` | JVM CPU 사용률 평균 (0~1) |
    | `app_jvm_cpu_utilization_pct_avg_5m` | JVM CPU 사용률 평균 (%) |
    | `app_jvm_memory_used_avg_5m` | JVM 메모리 사용량 평균 (bytes) |
    | `app_jvm_memory_used_max_5m` | JVM 메모리 사용량 최대 (bytes) |
    | `app_jvm_gc_count_5m` | 5분간 GC 횟수 |
    | `app_jvm_gc_duration_p95_5m` | GC 소요 시간 P95 |
- **DB 커넥션 풀** (HikariCP, `source=app`)
    
    
    | 파생 지표 | 설명 |
    | --- | --- |
    | `app_db_connection_wait_p95_5m` | 커넥션 획득 대기 시간 P95 |
    | `app_db_connection_use_p95_5m` | 커넥션 사용 시간 P95 |
- **Host** (`source=host`)
    
    
    | 파생 지표 | 설명 |
    | --- | --- |
    | `host_memory_usage_avg_5m` | 호스트 메모리 사용량 평균 (bytes) |
    | `host_network_rx_bytes_5m` | 5분간 수신 네트워크 트래픽 |
    | `host_network_tx_bytes_5m` | 5분간 송신 네트워크 트래픽 |
- **Alert용 집계 지표** (interval 1m, AlertManager rule 기준)
    
    
    | 파생 지표 | 설명 |
    | --- | --- |
    | `job:http_known_services:presence` | 서비스 활성 여부 (ServiceDown 감지용) |
    | `job:http_4xx_error_ratio:rate5m` | job 단위 4xx 에러율 |
    | `job:http_5xx_error_ratio:rate5m` | job 단위 5xx 에러율 |
</aside>

<aside>

### :s3: S3 저장 형식

유형별로 분리

파티션은 `{env}/{source}/` 구조로 구성
`{env}/{source}/year={YYYY}/month={MM}/day={DD}/hour={HH}/minute={mm}/`

| 버킷 | 파티션 구조 | 보존 기간 |
| --- | --- | --- |
| `logs-backup` | `dev/app/` 
`dev/host/` 
—————————
`prod/app/` 
`prod/host/` | 365일 |
| `traces-backup` | `dev/app/` 
—————————
`prod/app/` | 365일 |
| `metrics-backup` | `dev/app/` 
`dev/container/` 
`dev/host/` 
—————————
`prod/app/` 
`prod/container/` 
`prod/host/`  | 365일 |
| `athena-results` | Athena 쿼리 결과 저장 | 7일 |

장기 이력 조회는 Glue 카탈로그 + Athena로 SQL 쿼리 가능.

</aside>

---

### **2. 이상 감지**

**AMP → AlertManager → SNS**

- **`prometheus-rules.yaml`**
    - Recording rules. 원시 메트릭을 30s/1m 간격으로 집계해 파생 메트릭 생성
- **`prometheus-alerts.yaml`**
    - Alert rules. recording rule이 만든 파생 메트릭을 임계값과 비교해 alert 발생

```
prometheus-rules.yaml   → 파생 메트릭 계산 (interval 30s / 1m)
        ↓
prometheus-alerts.yaml  → 임계값 초과 여부 평가 (interval 1m)
        ↓
for 조건 충족 시 (2~5분 지속) → AlertManager가 SNS Topic으로 이벤트 발행
        ↓
SNS Topic으로 이벤트 발행
```

---

### **3. AI 분석**

**SNS → Lambda → LangGraph Agent**

<aside>

Lambda(`observability-agent`)가 실행되면 → `lambda_handler()` 진입 → 여러 분기로 나눠짐

- **Lambda 함수 -** `main.tf`에서 생성, `{project_name}-observability-agent`
- **핸들러** - `bedrock_agent_runtime_handler.py`의 `lambda_handler()`, Lambda 함수의 진입점으로 지정됨
- **나머지 파일들** - 핸들러 파일 안에서 임포트해서 쓰는 Python 모듈들
</aside>

SNS 트리거를 받은 Lambda가 Slack에 요약 메시지를 전송하고, 운영자가 분석 버튼을 클릭하면 LangGraph 실행

**① SNS 수신 → Slack 요약 메시지 전송**

```
SNS → lambda_handler
  ├─ DynamoDB 중복 확인 (dynamodb_incident.py)
  │    └─ 동일 alert_name으로 ongoing 인시던트가 이미 존재하면(이미 진행중인 동일 사항이 있는지) 즉시 종료 (중복 Slack 전송 방지)
  ├─ AgentCore Memory 조회 → 유사 과거 이력 검색 (agentcore_memory.py)
  ├─ Slack 요약 메시지 전송 (slack_templates.build_simple_alert_message)
  └─ DynamoDB ongoing 등록 (메모리 조회 결과 포함)
```

**② 분석 버튼 클릭 → LangGraph RCA 실행**

Slack이 3초 안에 응답을 요구하기 때문에, Lambda는 즉시 200을 반환하고 자기 자신을 비동기로 다시 호출

```
Slack 버튼 클릭 → lambda_handler (bedrock_agent_runtime_handler.py)
  ├─ Slack 메시지 "⏳ 분석 중..." 업데이트
  ├─ 비동기 self-invoke (_action=run_analysis)  ← 자기 자신을 비동기 호출
  └─ Slack에 200 즉시 반환 후 종료

  self-invoke → lambda_handler
    └─ _run_analysis_lambda() (bedrock_agent_runtime_handler.py)
         └─ AgentCore Runtime ARN 있으면 → 컨테이너 호출 - 이 경우에는 Agentcore Runtime으로 Async해서 분석하는거고 (이게 정상)
              └─ agent_runtime_app.py 실행
                   └─ _run_analysis_lambda() 임포트해서 호출
            ARN 없으면 (fallback) → _run_analysis_lambda() 직접 실행 - 런타임이 호출이 안되는 경우에는 async없이 람다로 진행하는거임
                                                                      (런타임 관련 코드 수정, 배포가 안된 상황을 가정하고 만들어 둔 거)
```

**LangGraph RCA** (`graph_agent_with_memory.py`)

```
memory_node    → agentcore_memory.py로 과거 유사 이력 조회
     ↓
rca_node
  Collector (graph_agent_with_memory.py에 정의된 Strands Agent)
    └─ tools: sub-agent 3개를 호출해 데이터 수집
         ├─ metrics_agent      → fetch_amp_metric, fetch_cloudwatch_metric 등 메트릭 조회
         ├─ logs_agent         → fetch_logs, fetch_traces, fetch_cloudwatch_logs 등 로그 조회
         └─ infrastructure_agent → fetch_ec2_status, fetch_rds_status, fetch_cloudtrail_events 등
			     ※ tool 호출 4회 제한 - 프롬프트로만 명시
           ※ dev 환경에서는 infrastructure_agent 제외 (로컬 Docker → AWS API 조회 불가)
           ※ topology_prompt.py로 dev/prod 환경 컨텍스트 주입
  Writer   (graph_agent_with_memory.py에 정의된 Strands Agent)
    └─ Collector 수집 결과를 RCA 리포트로 작성
  Reviewer (graph_agent_with_memory.py에 정의된 Strands Agent)
    └─ 증거 충분 여부 판단 → FAIL이면 Collector 재실행 (MAX_ITERATIONS = 3 - 루프 최대 3회)
     ↓
memory_save_node → dynamodb_incident.py로 DynamoDB 업데이트
    └─ SNS 수신 단계에서 이미 DynamoDB ongoing 등록이 완료된 상태
         → ongoing 인시던트가 이미 존재하면 스킵 (정상 플로우에서는 항상 스킵)
         → fallback: SNS 등록이 누락된 경우에만 여기서 신규 저장
     ↓
_run_analysis_lambda()에서 Slack API로 분석 결과 직접 전송
```

**③ 조치 완료 버튼 클릭 → 메모리 저장**

```
Slack 버튼 클릭 → lambda_handler (bedrock_agent_runtime_handler.py)
  ├─ 운영자 조치 내용 수신 (Slack modal)
  ├─ DynamoDB에서 root_cause 조회  → dynamodb_incident.py
  │    └─ RCA에서 분석된 원인을 AgentCore 저장 시 함께 사용하기 위해 먼저 꺼냄
  ├─ AgentCore Memory에 이력 저장  → agentcore_memory.py
  │    └─ 원인 / 조치 내용 / 소요 시간 → 이후 유사 알람 시 참조
  ├─ DynamoDB ongoing 삭제         → dynamodb_incident.py
  │    └─ 인시던트가 해결됐으므로 ongoing 상태 제거 (중복 알람 방지 해제)
  └─ Slack에 복구 완료 블록 append → slack_templates.py (build_resolved_append_blocks)
```

- **lambda_package 구성**
    
    
    | 파일 | 배포 형태 | 역할 |
    | --- | --- | --- |
    | `bedrock_agent_runtime_handler.py` | **Lambda 함수** — `lambda_handler()` 가 진입점 | SNS 수신, Slack 전송, 버튼 클릭 처리, AgentCore 위임 |
    | `agent_runtime_app.py` | **AgentCore Runtime 컨테이너** — `BedrockAgentCoreApp` 이 진입점 | `bedrock_agent_runtime_handler._run_analysis_lambda()` 를 직접 임포트해서 실행 |
    | `graph_agent_with_memory.py` | `bedrock_agent_runtime_handler.py` 에서 임포트 | LangGraph RCA 엔진 — `build_graph()` 로 그래프 생성, memory / rca / memory_save 노드 구성 |
    | `agents_aws.py` | `graph_agent_with_memory.py` 에서 임포트 | AMP·OpenSearch·EC2·RDS·CloudWatch·CloudTrail·ELB·ASG 조회 함수 + 3개 sub-agent (`metrics_agent`, `logs_agent`, `infrastructure_agent`) |
    | `agentcore_memory.py` | `bedrock_agent_runtime_handler.py`, `graph_agent_with_memory.py` 에서 임포트 | AgentCore Memory 읽기/쓰기 |
    | `dynamodb_incident.py` | `bedrock_agent_runtime_handler.py`, `graph_agent_with_memory.py` 에서 임포트 | DynamoDB ongoing 테이블 관리, 중복 알람 방지 |
    | `slack_templates.py` | `bedrock_agent_runtime_handler.py` 에서 임포트 | 알람·분석·해결 Slack 블록 빌드 |
    | `topology_prompt.py` | `agents_aws.py` 에서 임포트 | dev/prod 환경 구조를 AI 프롬프트에 주입 |
    | `cw_logger.py` | 전체 파일에서 임포트 | CloudWatch 로그 출력 유틸 |

---

### **4. 결과 전달**

**→ Slack (자동 리포트)**

- **① 알람 요약 메시지 수신**
    
    AlertManager 발화 시 Slack에 요약 메시지 자동 전송. 과거 유사 이력이 있으면 이전 원인·조치도 함께 표시
    
    ```
    알람명 / 심각도 / 감지 시각 / 서비스 정보 / 알람 설명
    유사 과거 사례 (있는 경우): 발생 횟수 / 평균 소요 시간 / 이전 원인 / 이전 조치
    버튼: [🔍 분석 요청]  [✅ 조치 완료]
    ```
    
- **② 분석 요청 버튼 클릭**
    
    메시지에 "⏳ AI 분석 중..." 상태 메시지가 하단에 추가되고, LangGraph RCA가 백그라운드에서 실행
    
- **③ 분석 완료 — 결과 append**
    
    RCA 완료 후 기존 메시지 아래에 분석 결과 블록이 추가됨
    
    ```
    장애 요약
    추정 원인
    핵심 근거 (metrics / logs / traces / infra)
    추천 조치사항 (즉시 조치 + 후속 조치)
    참조 런북 (해당 시)
    버튼: [✅ 조치 완료]
    ```
    
- **④ 조치 완료 버튼 클릭**
    
    운영자가 실제 조치 내용을 입력하면 해결 블록이 append되고, AgentCore 장기 메모리에 저장됨
    
    ```
    복구 시각 / 소요 시간
    🛠 조치 내용 (운영자 입력)
    → AgentCore Memory에 저장 (이후 유사 알람 시 참조)
    ```
    

---

### **Chatbot**

- **구성 파일**
    
    
    | 파일 | 역할 |
    | --- | --- |
    | `app.py` | FastAPI 서버 — HTTP 엔드포인트 정의, 에이전트 호출 |
    | `chat_agent.py` | Strands Agent 정의 — 툴 구성, 메모리 관리, 요약 생성 |
    | `database.py` | DynamoDB 대화 저장소 — 메시지·요약 읽기/쓰기 |
- **API 엔드포인트** (`app.py`)
    
    
    | 메서드 | 경로 | 설명 |
    | --- | --- | --- |
    | `GET` | `/` | 챗봇 HTML UI 반환 |
    | `GET` | `/conversations` | 대화 목록 조회 |
    | `POST` | `/conversations` | 새 대화 생성 |
    | `GET` | `/conversations/{cid}` | 특정 대화 + 메시지 조회 |
    | `DELETE` | `/conversations/{cid}` | 대화 삭제 (DynamoDB + in-process 캐시 동시 제거) |
    | `POST` | `/conversations/{cid}/chat` | 메시지 전송 → 에이전트 응답 반환 |
- **Agent** (`chat_agent.py`)
    - 모델: `claude-3-5-haiku` (Bedrock, us-east-1)
    - 세션별 Agent 인스턴스를 in-process 딕셔너리(`_session_agents`)에 캐싱
    - `SummarizingConversationManager` — 세션 내 컨텍스트 창 초과 시 자동 요약 (최근 20턴 보존)
    
    사용 툴 17개 (`agents_aws.py` 임포트):
    
    | 툴 | 설명 |
    | --- | --- |
    | `fetch_amp_metric` | AMP 메트릭 조회 |
    | `fetch_logs` | OpenSearch 애플리케이션 로그 조회 |
    | `fetch_traces` | OpenSearch 분산 트레이스 조회 |
    | `fetch_ec2_status` | EC2 인스턴스 상태 조회 |
    | `fetch_rds_status` | RDS 상태 조회 |
    | `fetch_cloudwatch_metric` | CloudWatch 메트릭 조회 |
    | `fetch_cloudwatch_alarms` | CloudWatch 알람 상태 조회 |
    | `fetch_cloudwatch_logs` | CloudWatch 로그 조회 |
    | `fetch_cloudtrail_events` | CloudTrail 변경 이력 조회 |
    | `fetch_elb_health` | ELB 헬스 상태 조회 |
    | `fetch_autoscaling_activity` | Auto Scaling 활동 이력 조회 |
    | `search_incident_history` | 과거 인시던트 이력 검색 |
    | `get_ongoing_alarms` | 현재 발화 중인 알람 목록 |
    | `get_active_services` | 현재 메트릭 전송 중인 서비스 목록 |
    | `query_historical_logs` | S3/Athena 장기 로그 이력 조회 |
    | `query_historical_traces` | S3/Athena 장기 트레이스 이력 조회 |
    | `query_log_error_summary` | S3/Athena 에러 통계 집계 |
    
    AgentCore Memory (`agent_core_memory`) 툴은 `AGENTCORE_MEMORY_ID` 환경변수가 설정된 경우 추가로 제공됨.
    
- **DynamoDB 대화 저장** (`database.py`)
    
    테이블 2개
    
    - `chatbot-conversations` - 대화 메타 (conversation_id, title, created_at, updated_at)
    - `chatbot-messages` - 메시지 + 요약 (PK: conversation_id, SK: message_id, role, content, is_summary)
    
    요약
    
    - 비요약 메시지가 **40개**를 초과하면 오래된 메시지를 Bedrock으로 요약 생성 후 DynamoDB에 저장
    - 에이전트 컨텍스트 로드 시 `get_context_messages()` - **최신 요약본 + 최근 20턴**만 전달
- **메모리**
    
    
    | 계층 | 저장소 | 범위 |
    | --- | --- | --- |
    | 단기 - 세션 내 | in-process Agent 캐시 + SummarizingConversationManager | 프로세스 살아있는 동안 |
    | 단기 - 재시작 후 | DynamoDB 요약본 + 최근 20턴 복원 | 서버 재시작 후에도 유지 |
    | 장기 - 대화 간 | AgentCore Memory (`agent_core_memory` 툴) | 대화 세션 넘어서 영구 보존 |
    
    **장기 메모리(AgentCore) 전환 기준**
    
    별도 코드 로직 없이 시스템 프롬프트 지시만으로 에이전트가 판단
    
    - **조회**: 대화 시작 시 항상 `agent_core_memory(retrieve)` 호출
    - **저장**: 반복적으로 언급되는 서비스명 / 장애 패턴 / 환경 설정을 Claude가 중요하다 판단하면 `agent_core_memory(record)` 호출
    
    명시적인 임계값(N턴 후 저장 등)은 없으며, 저장 여부는 에이전트 판단 - 프롬프트로밖에 명시 안해놨음
    
    ```powershell
    ## 장기 메모리 사용 원칙
    - 대화 시작 시 사용자의 환경/관심사와 관련된 메모리를 agent_core_memory(retrieve)로 조회할 것
    - 반복적으로 언급되는 서비스명, 장애 패턴, 환경 설정은 agent_core_memory(record)로 저장할 것
    ```
    

메시지는 대화 삭제 전까지 DynamoDB에 계속 누적

```
메시지 전송마다 chatbot-messages에 저장 (is_summary=False)
        ↓
비요약 메시지 수가 40개 초과 시
        ↓
오래된 메시지들을 Bedrock으로 요약 생성
        ↓
요약본을 is_summary=True로 저장 (원본은 삭제하지 않음)
        ↓
에이전트 컨텍스트: 최신 요약본 1개 + 최근 20턴만 전달
UI 표시:          is_summary=False 메시지만 표시
```

---