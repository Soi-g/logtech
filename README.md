# AWS Observability Platform

---

## 인프라 (Terraform)

| 파일 | 역할 |
|------|------|
| `main.tf` | VPC / 네트워크 / OpenSearch / AMP / S3 / Gateway EC2 |
| `alert_infra.tf` | SNS 토픽 + Lambda 함수 (알람 진입점) + IAM |
| `agentcore_runtime.tf` | AgentCore Runtime (Docker 컨테이너 실행 환경) |
| `agentcore_memory.tf` | AgentCore Memory Store (장기 장애 이력) |
| `chatbot_db.tf` | DynamoDB (ongoing 인시던트 추적) |
| `opensearch_serverless.tf` | OpenSearch Serverless (벡터 검색, 구버전 잔재) |
| `bedrock_agent_memory.tf` | Bedrock Knowledge Base (런북 저장) |
| `aws_customer_app/main.tf` | 고객 앱 EC2 2대 (Frontend / Backend) + RDS |

---

## EC2 User Data (부팅 스크립트)

| 파일 | 역할 |
|------|------|
| `user_data.sh` | **Gateway EC2** — OTel Collector Gateway + Prometheus + Alertmanager + Grafana + OpenSearch 설치/설정 |
| `aws_customer_app/templates/user_data_frontend.sh.tpl` | **Frontend EC2** — Flask + Thymeleaf + loadgenerator (Docker Compose) + OTel Collector Agent (sidecar) |
| `aws_customer_app/templates/user_data_backend.sh.tpl` | **Backend EC2** — SpringBoot + OTel Collector Agent (sidecar) |

---

## OTel / 메트릭 설정

| 파일 | 역할 |
|------|------|
| `prometheus-rules.yaml` | Prometheus Recording Rules — 파생 메트릭 정의 (`app_http_server_errors_5m`, `app_jvm_*` 등) |

---

## RCA 엔진 (`lambda_package/`)

### 핵심 실행 흐름

SNS 알람
└─ Lambda (alert_infra.tf)
└─ bedrock_agent_runtime_handler.py  ← 진입점
└─ AgentCore Runtime 호출
└─ agent_runtime_app.py    ← Runtime entrypoint
└─ graph_agent_with_memory.py  ← LangGraph RCA 루프
└─ agents_aws.py  ← 실제 데이터 조회 툴



### 파일별 역할

| 파일 | 역할 |
|------|------|
| `agent_runtime_app.py` | AgentCore Runtime entrypoint. HTTP 요청 수신 → `_run_analysis_lambda()` 비동기 실행 |
| `bedrock_agent_runtime_handler.py` | Lambda + AgentCore Runtime 공용 핸들러. 알람 파싱 → RCA 실행 → Slack 전송. Slack 버튼(분석 요청 / 조치 완료) 처리 |
| `graph_agent_with_memory.py` | **RCA 핵심 엔진**. LangGraph 기반 `memory → rca → memory_save` 그래프. Collector→Writer→Reviewer 루프 (최대 2회). 3개 프롬프트 정의 |
| `agents_aws.py` | **데이터 조회 툴**. `metrics_agent` / `logs_agent` / `infrastructure_agent` — Strands `@tool`로 Collector가 호출 |
| `slack_templates.py` | Slack Block Kit 템플릿. `build_simple_alert_message` (최초 알람 카드) / `build_analysis_append_blocks` (분석 결과 append) / `build_resolved_append_blocks` (조치 완료) |
| `agentcore_memory.py` | AgentCore Memory CRUD. 장기 장애 이력 저장/조회 (조치 완료 시에만 저장) |
| `dynamodb_incident.py` | DynamoDB ongoing 인시던트 추적. 알람 발화 ~ 조치 완료 사이 임시 상태 저장 |
| `cw_logger.py` | CloudWatch Logs 로거 래퍼 |
| `runbooks_aws.py` | 런북 관련 유틸 (현재 미사용) |
| `aoss_index_creator.py` | OpenSearch 인덱스 생성 스크립트 (초기 설정용) |

---

## RCA 3-에이전트 구조 (`graph_agent_with_memory.py`)

Collector (COLLECTOR_PROMPT)
├─ PHASE 1: 알람 정보로 정당화되는 툴 병렬 호출
│    예) Http5xxErrorRate → metrics + logs + infrastructure 동시
└─ PHASE 2: PHASE 1 결과가 새 증거 가리킬 때만 드릴다운
예) RDS STOPPED → EC2 상태 추가 확인

Writer (WRITER_PROMPT)
└─ 조사 요약 → 엄격한 JSON 리포트 생성
{incident_summary, likely_root_causes, immediate_actions,
follow_up_actions, evidence_summary, severity, confidence, evidence}

Reviewer (REVIEWER_PROMPT)
└─ PASS / FAIL 품질 검증
PASS → Slack 전송
FAIL + iteration < 2 → GAPS 피드백 → Collector 재실행



---

## 데이터 조회 툴 (`agents_aws.py`)

| 툴 | 데이터 소스 | 조회 내용 |
|---|---|---|
| `metrics_agent` | AMP (Prometheus) | HTTP 에러율/latency, JVM, 컨테이너, RDS/EC2 CloudWatch 메트릭 |
| `logs_agent` | OpenSearch | 앱 로그 (logs-app 인덱스), 트레이스 |
| `infrastructure_agent` | AWS API (boto3) | EC2/RDS 현재 상태, CloudTrail 이벤트 (prod 전용) |

---

## Slack 메시지 흐름

[1] 알람 발화 → chat_postMessage → 알람 카드 (심각도/감지시각/설명/버튼)

[2] "분석 요청" 버튼 클릭
→ chat_update (ts 유지) → "⏳ AI 분석 중..." append

[3] RCA 완료
→ chat_update (ts 유지) → 분석 결과 블록 append
(장애 요약 / 추정 원인 / 핵심 근거 / 추천 조치 / 조치완료 버튼)

[4] "조치 완료" 버튼 클릭
→ 모달 입력 → AgentCore Memory 장기 저장 → resolved 블록 append



---

## 빌드 / 배포

| 파일 | 역할 |
|------|------|
| `build_agentcore.ps1` | AgentCore Runtime Docker 이미지 빌드 → ECR 푸시 → AgentCore Runtime 업데이트 |
| `scripts/build_lambda_layer.sh` | Lambda Layer (Python 패키지) 빌드 |
| `scripts/setup_grafana_datasources.sh` | Grafana 데이터소스 자동 설정 |

> **주의**: `graph_agent_with_memory.py` / `agents_aws.py` 수정 시 반드시 `build_agentcore.ps1` 재실행 필요 (Lambda 재배포만으로는 반영 안 됨)

---

## 앱 토폴로지 (prod 환경)

Frontend EC2
├─ todoui-flask    (Docker, port 5001) → springboot:8080 → RDS PostgreSQL
├─ todoui-thymeleaf (Docker, port 8090) → springboot:8080 → RDS PostgreSQL
└─ loadgenerator   (Docker)

Backend EC2
└─ springboot      (Docker, port 8080) → RDS PostgreSQL
(log-platform-dev-customer-postgres)

AMP job 라벨: todolist/flask, todolist/thymeleaf, todolist/springboot
