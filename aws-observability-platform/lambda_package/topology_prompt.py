"""
플랫폼 토폴로지 및 환경 정보 — AI 에이전트 주입용
참조 문서: ec2_app_topology.md (prod), vm_app_topology.md (dev)
환경 변경 시 이 파일과 두 md 파일을 함께 수정할 것 (Docker 재빌드 필요).
"""

TOPOLOGY_CONTEXT = """
## 앱 토폴로지

### 환경 구분
- "dev"  : 온프레미스 VM (VMware) + Docker Compose — 로컬 개발/교육 환경
- "prod" : AWS 고객 앱 스택 — EC2 + RDS, 실제 배포 환경
- 환경 불명확 → prod 가정; 알람에 "dev" 명시된 경우에만 dev 처리

---

### 공통 서비스 구조 (dev/prod 동일)

서비스 네임스페이스: todolist
서비스 이름 (service.name): springboot | thymeleaf | flask | host-logs | host-metrics

트래픽 체인 (동일):
  인터넷/사용자
    └──► todoui-flask(:5001)      ──► todobackend-springboot(:8080) ──► DB(:5432)
    └──► todoui-thymeleaf(:8090)  ──► todobackend-springboot(:8080) ──► DB(:5432)

핵심 규칙:
- flask/thymeleaf 5xx 알람 → 원인은 springboot 또는 DB에 있을 가능성이 높음
- DB 연결 에러는 springboot 로그에 SEVERE 레벨로 기록됨 (flask/thymeleaf 로그 아님)
- logs_agent로 로그 조회 시 백엔드 이슈는 반드시 service=springboot 로 조회할 것

AMP job 라벨 (service_namespace/service_name 형식 필수):
  todolist/flask | todolist/thymeleaf | todolist/springboot | todolist/host-metrics

---

### prod 환경 (AWS EC2)

플랫폼:
  - Gateway EC2: log-platform-dev-otel-collector (ap-northeast-2, t2.micro, Elastic IP 고정)
  - AMP: log-platform-dev-amp
  - OpenSearch: log-platform-dev (logs-app / logs-host / traces-app 인덱스)
  - DynamoDB: log-platform-dev-incident-ongoing (진행 중 인시던트 중복 방지)
  - Lambda: log-platform-dev-alert-handler (SNS → Slack 알람·분석)

고객 앱 (별도 Customer VPC, CIDR 10.1.0.0/16):
  - Frontend EC2: log-platform-dev-customer-frontend (t3.micro, 퍼블릭)
      컨테이너: todoui-flask(5001→5000), todoui-thymeleaf(8090→8090), loadgenerator
      Flask 이미지: ichanee/todoui-flask:latest
      Thymeleaf 이미지: ghcr.io/lftraining/lfs148-code-todoui-thymeleaf:v2404
      BACKEND_URL(flask): http://{backend_private_ip}:8080/todos/
      BACKEND_URL(thymeleaf): http://{backend_private_ip}:8080/
  - Backend EC2: log-platform-dev-customer-backend (t3.micro, 퍼블릭)
      컨테이너: todobackend-springboot(8080→8080)
      이미지: ghcr.io/lftraining/lfs148-code-todobackend-springboot:v2404
      POSTGRES_HOST: {rds_endpoint} (RDS 주소)
  - RDS: log-platform-dev-customer-postgres (PostgreSQL 16, db.t3.micro)
      DB: mydb, 사용자: matthias, 비공개 엔드포인트

보안 그룹 구조:
  - frontend-sg: 인터넷 → :5001(flask), :8090(thymeleaf) 허용
  - backend-sg: frontend-sg → :8080(springboot) 만 허용 (인터넷 직접 차단)
  - rds-sg: backend-sg → :5432(postgres) 만 허용

OTel 데이터 흐름 (prod):
  앱 컨테이너 → otelcol(systemd, host.docker.internal:4317) → Gateway EC2(:14317) → AMP/OpenSearch

infrastructure_agent 사용 가능 리소스 (prod 전용):
  - RDS: fetch_rds_status("log-platform-dev-customer-postgres")
  - Frontend EC2: fetch_ec2_status(name_filter="log-platform-dev-customer-frontend")
  - Backend EC2: fetch_ec2_status(name_filter="log-platform-dev-customer-backend")
  - CloudTrail: SG 변경(AuthorizeSecurityGroupIngress, RevokeSecurityGroupIngress), RDS 변경(ModifyDBInstance, RebootDBInstance)

---

### dev 환경 (온프레미스 VM + Docker Compose)

위치: 로컬 VMware VM, LFS148-code_v2/docker-compose.yaml
Docker 네트워크: todonet (bridge)

컨테이너:
  - postgresdb: postgres:16.3, 포트 5432, DB=mydb, user=matthias, pwd=password
      ※ 컨테이너이므로 RDS와 달리 AWS API로 상태 조회 불가
  - todobackend-springboot: 8080→8080, SPRING_PROFILES_ACTIVE=prod, DB=postgresdb:5432
  - todoui-thymeleaf: 8090→8090, BACKEND_URL=http://todobackend-springboot:8080/
  - todoui-flask: 5001→5000, BACKEND_URL=http://todobackend-springboot:8080/todos/
      OTEL_SEMCONV_STABILITY_OPT_IN=http (HTTP 라벨 Java 형식 통일)
  - loadgenerator: 합성 트래픽 생성 (flask, thymeleaf 대상)
  - otelcol: OTel Collector Contrib, 포트 4317/4318, gateway EC2로 전송

OTel 데이터 흐름 (dev):
  앱 컨테이너 → otelcol:4317 (todonet 내부) → Gateway EC2 (IP from .env) → AMP/OpenSearch

dev 환경 분석 핵심 제약:
  - infrastructure_agent: AWS RDS/EC2 전용 → 로컬 Docker 컨테이너 상태 조회 불가
  - postgresdb 중지 여부 → AWS API 없음; springboot SEVERE 로그(HikariPool timeout)로만 간접 확인
  - dev 로그 조회 시 반드시 environment="dev" 지정 (OpenSearch에 prod 로그와 혼재)

---

### OTel 리소스 속성 (공통 스키마)

| 속성 | prod 값 | dev 값 |
|---|---|---|
| resource.service.namespace | todolist | todolist |
| resource.deployment.environment | prod | dev |
| resource.service.name | springboot/thymeleaf/flask/host-logs/host-metrics | 동일 |
| source | app/container/host/sys | 동일 |

OpenSearch 인덱스:
  - logs-app: 앱 OTLP 로그 (environment 필드로 dev/prod 구분)
  - logs-host: 호스트 OS 로그 (syslog/auth.log)
  - traces-app: 분산 트레이스 span
"""
