# 프로젝트 구조

> 마지막 업데이트: 2026-03-26
> Terraform 모듈화 완료 기준

---

## 디렉터리 트리

```
aws-observability-platform/
│
├── main.tf                      # Terraform 진입점 — 6개 모듈 호출
├── variables.tf                 # 루트 변수 정의
├── outputs.tf                   # 루트 출력값 (모듈 output 집계)
├── terraform.tfvars             # 실제 변수값 (gitignore 권장)
├── terraform.tfvars.example     # 변수 템플릿
│
├── # ── 루트에 남긴 Terraform 파일 (모듈화 불가 — 상호 참조) ──
├── bedrock_agent_memory.tf      # Bedrock Agent 정의, incident DynamoDB, lambda policy 추가
├── agentcore_runtime.tf         # ECR 레포, AgentCore IAM role, lambda invoke 권한
├── agentcore_memory.tf          # AgentCore Memory IAM 권한 (lambda role에 attach)
├── opensearch_serverless.tf     # AOSS 관련 (현재 전체 주석처리 — 미사용)
│
├── user_data.sh                 # EC2 OTel Collector 부팅 스크립트 (Terraform templatefile)
├── requirements_layer.txt       # Lambda Layer 빌드용 pip 패키지 목록
├── lambda_layer/                # 빌드된 Lambda Layer 패키지 (lambda_layer.zip 원본)
│
├── agentcore/                   # AgentCore Runtime 빌드/배포 도구
│   ├── Dockerfile               # AgentCore 컨테이너 이미지 (python:3.12-slim + bedrock-agentcore)
│   └── build_agentcore.ps1      # ECR 빌드·푸시 + Runtime 생성/업데이트 (프로젝트 루트에서 실행)
│
├── topology/                    # 앱 토폴로지 문서 (사람용 참조)
│   ├── ec2_app_topology.md      # AWS prod 환경 — EC2/RDS/VPC 구조
│   └── vm_app_topology.md       # 온프레미스 dev 환경 — Docker Compose 구조
│
├── runbooks/                    # 장애 대응 런북 (Lambda가 S3에서 로드, 향후 재활성화 예정)
│   ├── HighHttpErrorRate.md
│   ├── HighHttpLatency.md
│   ├── HighDbConnectionPending.md
│   ├── HighJvmCpu.md
│   ├── HighJvmMemory.md
│   └── PostMortem-Template.md
│
├── scripts/                     # 운영 유틸리티 스크립트
│   ├── build_lambda_layer.sh    # Lambda Layer zip 빌드
│   ├── setup_grafana_datasources.sh
│   ├── setup_indexes.ps1        # OpenSearch 인덱스 초기 생성
│   ├── create_indexes_manual.py
│   ├── create_aoss_index_local.py
│   └── *.json                   # API 페이로드 샘플
│
├── lambda_package/              # Lambda 함수 소스 (ZIP → S3 → Lambda)
│   ├── bedrock_agent_runtime_handler.py  # Lambda 핸들러 — SNS 수신 → AgentCore 위임 or LangGraph 직접 실행
│   ├── graph_agent_with_memory.py        # LangGraph 분석 그래프 (metrics/logs/traces/infra 에이전트)
│   ├── agent_runtime_app.py              # AgentCore Runtime 엔트리포인트 (컨테이너 실행용)
│   ├── agents_aws.py                     # AWS 도구 함수 (AMP, OpenSearch, EC2, RDS, CloudTrail)
│   ├── agentcore_memory.py               # AgentCore Memory 읽기/쓰기
│   ├── dynamodb_incident.py              # DynamoDB incident-ongoing 테이블 중복 방지
│   ├── slack_templates.py                # Slack 메시지 포맷터
│   ├── topology_prompt.py                # 앱 토폴로지 컨텍스트 (AI 주입용 — dev/prod 환경 구조)
│   ├── cw_logger.py                      # CloudWatch 로그 유틸
│   ├── runbooks_aws.py                   # 런북 S3 조회 (현재 비활성화, 향후 재활성화 예정)
│   └── aoss_index_creator.py             # AOSS 인덱스 생성 유틸 (미사용)
│
└── modules/                     # Terraform 모듈
    │
    ├── networking/              # VPC · 서브넷 · 라우팅 · 보안그룹
    │   ├── main.tf              # VPC(10.0.0.0/16), public/private subnet, IGW, NAT GW, route table
    │   │                        # SG: otel-collector-sg, opensearch-sg
    │   ├── variables.tf         # project_name, aws_region
    │   └── outputs.tf           # vpc_id, vpc_cidr_block, public/private_subnet_id, *_sg_id
    │
    ├── storage/                 # S3 버킷 6개 + lifecycle
    │   ├── main.tf              # logs-backup(30d), traces-backup(30d), metrics-backup(365d)
    │   │                        # runbooks, deploy(versioning ON), athena-results(7d)
    │   ├── variables.tf         # project_name, account_id
    │   └── outputs.tf           # *_id, *_arn (버킷별)
    │
    ├── observability/           # AMP · OpenSearch · Glue/Athena · Cognito · Bedrock Agent IAM
    │   ├── main.tf              # AMP workspace + derived-metrics 기록 규칙
    │   │                        # OpenSearch 2.11 (t3.small, VPC private subnet)
    │   │                        # Glue catalog DB + 테이블 4개(otel_logs_app/host, otel_traces, otel_metrics)
    │   │                        # Athena workgroup
    │   │                        # Cognito user pool (client_credentials JWT — Envoy 인증)
    │   │                        # IAM: bedrock-agent-role
    │   ├── variables.tf         # project_name, aws_region, account_id, subnet/sg, opensearch 인증, S3 ARN들
    │   ├── outputs.tf           # amp_*, opensearch_*, bedrock_agent_role_arn, cognito_*, athena/glue 이름
    │   └── prometheus-rules.yaml  # AMP 기록 규칙 정의 (derived-metrics)
    │
    ├── compute/                 # EC2 OTel Collector · IAM · EIP
    │   ├── main.tf              # IAM role/policy/profile (otel-collector)
    │   │                        # EC2 t2.micro + user_data.sh templatefile
    │   │                        # EIP (고정 IP)
    │   ├── variables.tf         # ec2 설정, 네트워크, S3/OpenSearch/AMP/Cognito/DynamoDB 값들
    │   └── outputs.tf           # otel_collector_public_ip, otel_collector_role_arn
    │
    ├── alerting/                # Lambda · SNS · AMP 알람룰 · Alertmanager
    │   ├── main.tf              # Lambda SG, SNS topic
    │   │                        # IAM: lambda-agent-role (AMP/OpenSearch/Bedrock/EC2/RDS/CloudTrail)
    │   │                        # Lambda 패키징(archive_file) → S3 → Lambda Layer → Lambda Function
    │   │                        # SNS subscription, Function URL
    │   │                        # AMP alert rules (ServiceDown, Http4xx/5xx, Unexpected4xx)
    │   │                        # AMP Alertmanager (SNS receiver, inhibit rules)
    │   ├── variables.tf         # 네트워크, S3, OpenSearch, AMP, Slack, Bedrock Agent ID, DynamoDB 테이블
    │   └── outputs.tf           # lambda_agent_role_id (bedrock/agentcore tf에서 policy 추가용), sns_topic_arn, agent_function_url
    │
    └── chatbot/                 # 챗봇 DynamoDB · 코드 패키징 · S3 업로드
        ├── main.tf              # DynamoDB: chatbot-conversations, chatbot-messages
        │                        # archive_file: chatbot 소스 + lambda_package 일부 → chatbot.zip → S3
        ├── variables.tf         # project_name, s3_deploy_id
        ├── outputs.tf           # conversations_table, messages_table, package_etag
        └── chatbot/             # 챗봇 앱 소스
            ├── app.py           # Flask 앱
            ├── chat_agent.py    # Bedrock 연동 에이전트
            ├── database.py      # DynamoDB 대화 저장
            ├── requirements.txt
            └── templates/index.html
```

---

## 모듈 간 의존성

```
networking ──────────────────────────────────────┐
                                                  │
storage ─────────────────────────────────────────┤
         │                                        │
         ▼                                        ▼
      observability ──────────────────────► compute
         │                                        ▲
         │                                        │
         ▼                                        │
      alerting ◄── bedrock_agent_memory.tf ───────┘
         │          (Bedrock Agent ID 전달)
         │
         ▼  (lambda_agent_role_id 출력)
      bedrock_agent_memory.tf
      agentcore_runtime.tf      ← module.alerting.lambda_agent_role_id 참조
      agentcore_memory.tf

chatbot ──► storage (deploy 버킷)
        └──► compute (테이블명 전달)
```

---

## 데이터 플로우 요약

```
[고객 앱 OTel] ──JWT(Cognito)──► Envoy:4317 ──► OTelCol(systemd):14317
                                                    │
                              ┌─────────────────────┼──────────────────────┐
                              ▼                      ▼                      ▼
                           AMP                  OpenSearch              S3 백업
                       (메트릭)           (logs-app/logs-host/    (logs/traces/metrics)
                           │               traces-app 인덱스)
                           │ AlertManager
                           ▼
                        SNS Topic
                           │
                           ▼
                     Lambda (알람 핸들러)
                           │
              ┌────────────┴────────────┐
              ▼                         ▼
       AgentCore Runtime          LangGraph (직접)
       (컨테이너, 최대 8h)        (Lambda 내 실행)
              │
              ▼
     Slack 분석 결과 전송
```

---

## 주요 실행 명령어

| 작업 | 명령어 |
|---|---|
| 인프라 배포 | `terraform apply` |
| AgentCore 빌드·배포 | `.\agentcore\build_agentcore.ps1` (프로젝트 루트에서) |
| Lambda Layer 빌드 | `./scripts/build_lambda_layer.sh` |
| OpenSearch 인덱스 생성 | `.\scripts\setup_indexes.ps1` |

---

## 루트에 남은 .tf 파일 이유

| 파일 | 모듈화 못한 이유 |
|---|---|
| `bedrock_agent_memory.tf` | Bedrock Agent → `module.alerting` 입력 & `module.alerting.lambda_agent_role_id` → policy 추가. 모듈 경계를 양방향으로 넘어 순환 위험 |
| `agentcore_runtime.tf` | `module.alerting.lambda_agent_role_id` 참조로 alerting 모듈 이후에 배치 필요 |
| `agentcore_memory.tf` | 동일 |
| `opensearch_serverless.tf` | 전체 주석처리 상태 (미사용) |
