# Application Topology: AWS 고객 앱 스택 (prod 환경)

## Overview

고객 앱 스택은 AWS에 배포된 3-tier 웹 애플리케이션 "todolist"이다.
별도 Customer VPC에 2개의 EC2와 1개의 RDS로 구성되며,
각 EC2에는 OTel Collector Agent(systemd)가 사이드카로 실행된다.

- **서비스 네임스페이스**: `todolist`
- **배포 환경 (텔레메트리)**: `prod` (Terraform `environment` 변수)
- **인프라**: AWS (VPC, EC2, RDS)
- **오케스트레이션**: Docker Compose (각 EC2 내)

---

## 플랫폼 인프라 (Platform VPC)

### VPC

| 항목 | 값 |
|---|---|
| VPC Name | `log-platform-dev-vpc` |
| CIDR | `10.0.0.0/16` |
| 퍼블릭 서브넷 | `10.0.1.0/24` (ap-northeast-2a) |
| 프라이빗 서브넷 | `10.0.2.0/24` (ap-northeast-2a) |

### Platform Gateway EC2 (`log-platform-dev-otel-collector`)

| 항목 | 값 |
|---|---|
| 인스턴스 타입 | t2.micro |
| 서브넷 | 퍼블릭 (`10.0.1.0/24`) |
| SG | `log-platform-dev-otel-sg` |
| Elastic IP | 고정 (매 재시작 시 유지) |

**SG 인바운드 규칙:**

| 포트 | 프로토콜 | 소스 | 목적 |
|---|---|---|---|
| 22 | TCP | 0.0.0.0/0 | SSH |
| 4317 | TCP | 0.0.0.0/0 | OTLP gRPC — Envoy 경유 (Cognito JWT 인증) |
| 14317 | TCP | 0.0.0.0/0 | OTLP gRPC — 직접 (고객 EC2 신뢰 경로) |
| 4318 | TCP | 0.0.0.0/0 | OTLP HTTP |
| 3000 | TCP | 0.0.0.0/0 | Grafana |
| 8000 | TCP | 0.0.0.0/0 | Chatbot |

**실행 중인 서비스:**
- `otelcol-contrib` v0.147.0 (systemd): 텔레메트리 수신·처리·전송
- Envoy (Docker): Cognito JWT 인증 프록시 (포트 4317/4318 → 14317/14318)
- Chatbot (Docker, port 8000): 채팅 기반 관측 인터페이스

### AWS 관리형 서비스

| 서비스 | 식별자 | 비고 |
|---|---|---|
| AMP | `log-platform-dev-amp` | 메트릭 저장 (ap-northeast-2) |
| OpenSearch | `log-platform-dev` | OpenSearch 2.11, t3.small.search, 10GiB gp3, VPC 프라이빗 서브넷 |
| DynamoDB | `log-platform-dev-incident-ongoing` | 진행 중 인시던트 중복 방지 테이블 |
| Lambda | `log-platform-dev-alert-handler` | SNS → Slack 알람·분석 처리 |
| SNS | `log-platform-dev-alerts` | AMP Alertmanager → Lambda |
| AgentCore Memory | `log_platform_dev_agentcore_memory-h0ndHU8STK` | 장기 인시던트 기억 저장 |
| Cognito User Pool | — | 외부 OTel Collector JWT 인증 |
| S3 | `log-platform-dev-logs-backup-347751175815` | 로그 백업 (30일 보관) |
| S3 | `log-platform-dev-traces-backup-347751175815` | 트레이스 백업 (30일 보관) |
| S3 | `log-platform-dev-metrics-backup-347751175815` | 메트릭 백업 (365일 보관) |
| S3 | `log-platform-dev-runbooks-347751175815` | 런북 문서 |
| S3 | `log-platform-dev-deploy-347751175815` | Lambda 코드·챗봇 패키지 배포용 |
| Athena | `log-platform-dev-observability` | S3 로그·트레이스 쿼리 |

**OpenSearch 인덱스:**
- `logs-app`: 앱 OTLP 로그 (springboot/flask/thymeleaf)
- `logs-host`: EC2 호스트 syslog/auth.log
- `traces-app`: 분산 트레이스 span

---

## 고객 앱 스택 (Customer VPC)

### VPC / 네트워크

| 항목 | 값 |
|---|---|
| VPC Name | `log-platform-dev-customer-vpc` |
| CIDR | `10.1.0.0/16` |
| 앱 서브넷 | `10.1.1.0/24` (ap-northeast-2a, 퍼블릭, 공개 IP 자동 할당) |
| DB 서브넷 | `10.1.2.0/24` (ap-northeast-2c, 프라이빗) |
| IGW | `log-platform-dev-customer-igw` |

### 보안 그룹

#### Frontend SG (`log-platform-dev-customer-frontend-sg`)

| 방향 | 포트 | 프로토콜 | 소스 | 목적 |
|---|---|---|---|---|
| 인바운드 | 22 | TCP | 0.0.0.0/0 | SSH |
| 인바운드 | 5001 | TCP | 0.0.0.0/0 | Flask UI (공개) |
| 인바운드 | 8090 | TCP | 0.0.0.0/0 | Thymeleaf UI (공개) |
| 아웃바운드 | all | all | 0.0.0.0/0 | Backend API, OTel gateway, 인터넷 |

#### Backend SG (`log-platform-dev-customer-backend-sg`)

| 방향 | 포트 | 프로토콜 | 소스 | 목적 |
|---|---|---|---|---|
| 인바운드 | 22 | TCP | 0.0.0.0/0 | SSH |
| 인바운드 | 8080 | TCP | frontend-sg 만 | Spring Boot REST API |
| 아웃바운드 | all | all | 0.0.0.0/0 | RDS, OTel gateway, 인터넷 |

#### RDS SG (`log-platform-dev-customer-rds-sg`)

| 방향 | 포트 | 프로토콜 | 소스 | 목적 |
|---|---|---|---|---|
| 인바운드 | 5432 | TCP | backend-sg 만 | PostgreSQL 접근 |
| 아웃바운드 | all | all | 0.0.0.0/0 | |

---

## Tier 1 — 프레젠테이션 계층 (Frontend EC2)

### 호스트

| 항목 | 값 |
|---|---|
| Name | `log-platform-dev-customer-frontend` |
| 인스턴스 타입 | t3.micro |
| AMI | Ubuntu 22.04 (ami-0c9c942bd7bf113a2) |
| 서브넷 | app-subnet (퍼블릭 IP 할당) |
| SG | frontend-sg |
| 루트 볼륨 | 10 GiB gp3 |

### 컨테이너 (Docker Compose `/opt/app/docker-compose.yaml`)

#### todoui-flask

| 항목 | 값 |
|---|---|
| 이미지 | `ichanee/todoui-flask:latest` |
| 포트 | 5001(host) → 5000(container) |
| BACKEND_URL | `http://{backend_private_ip}:8080/todos/` |
| OTel endpoint | `host.docker.internal:4317` |
| service.name | `flask` |
| deployment.environment | `prod` |
| 특이사항 | `OTEL_SEMCONV_STABILITY_OPT_IN=http` 설정 (Python OTel HTTP 라벨을 Java 형식과 통일) |

#### todoui-thymeleaf

| 항목 | 값 |
|---|---|
| 이미지 | `ghcr.io/lftraining/lfs148-code-todoui-thymeleaf:v2404` |
| 포트 | 8090(host) → 8090(container) |
| BACKEND_URL | `http://{backend_private_ip}:8080/` |
| OTel endpoint | `http://host.docker.internal:4317` |
| service.name | `thymeleaf` |
| deployment.environment | `prod` |

#### loadgenerator

| 항목 | 값 |
|---|---|
| 이미지 | `ghcr.io/lftraining/lfs148-code-simple-generator:v2404` |
| 역할 | Flask(:5001), Thymeleaf(:8090) 대상 합성 트래픽 생성 |
| 비고 | docker_stats 수집 제외 |

### OTel Collector Agent (Frontend EC2, systemd)

- 바이너리: `otelcol-contrib` v0.147.0
- 사용자: `otelcol` (docker, adm 그룹)
- config: `/etc/otelcol/config.yaml`
- file storage: `/var/lib/otelcol/file_storage`

**리시버:**

| 리시버 | 엔드포인트/소스 | 목적 |
|---|---|---|
| otlp (gRPC) | `0.0.0.0:4317` | flask, thymeleaf OTLP 수신 |
| docker_stats | `unix:///var/run/docker.sock` | 컨테이너 메트릭 (loadgenerator 제외) |
| hostmetrics | OS | CPU/메모리/디스크/네트워크/파일시스템 |
| filelog | `/var/log/syslog`, `/var/log/auth.log` | 호스트 OS 로그 |

**파이프라인 → 엑스포터:** 모두 `otlp/gateway` (`{gateway_ip}:14317`, insecure)

| 파이프라인 | 리시버 | source 라벨 |
|---|---|---|
| traces | otlp | app |
| metrics/app | otlp | app |
| metrics/container | docker_stats | container |
| metrics/host | hostmetrics | host |
| logs/app | otlp | app |
| logs/host | filelog | sys |

---

## Tier 2 — 애플리케이션 계층 (Backend EC2)

### 호스트

| 항목 | 값 |
|---|---|
| Name | `log-platform-dev-customer-backend` |
| 인스턴스 타입 | t3.micro |
| AMI | Ubuntu 22.04 |
| 서브넷 | app-subnet |
| SG | backend-sg |
| 루트 볼륨 | 10 GiB gp3 |

### 컨테이너 (Docker Compose `/opt/app/docker-compose.yaml`)

#### todobackend-springboot

| 항목 | 값 |
|---|---|
| 이미지 | `ghcr.io/lftraining/lfs148-code-todobackend-springboot:v2404` |
| 포트 | 8080(host) → 8080(container) |
| SPRING_PROFILES_ACTIVE | `prod` |
| POSTGRES_HOST | `{rds_endpoint}` (Terraform 주입) |
| OTel endpoint | `http://host.docker.internal:4317` |
| service.name | `springboot` |
| deployment.environment | `prod` |

### OTel Collector Agent (Backend EC2, systemd)

Frontend EC2와 동일한 구성. 파이프라인도 동일.
- **차이점**: docker_stats에서 loadgenerator 제외 설정 없음 (springboot 단독)

---

## Tier 3 — 데이터 계층 (RDS)

| 항목 | 값 |
|---|---|
| 식별자 | `log-platform-dev-customer-postgres` |
| 엔진 | PostgreSQL 16 |
| 인스턴스 클래스 | db.t3.micro |
| 스토리지 | 20 GiB gp2 |
| DB 이름 | `mydb` |
| 사용자 | `matthias` |
| 비밀번호 | `password` |
| 공개 접근 | 불가 (private endpoint) |
| Multi-AZ | 비활성 (single-AZ) |
| CloudWatch 로그 | postgresql 로그 활성화 |
| 서브넷 그룹 | app-subnet(ap-northeast-2a) + db-subnet(ap-northeast-2c) |
| SG | rds-sg (backend-sg에서만 5432 접근 허용) |

---

## 서비스 통신 맵

```
인터넷
  │
  ├──► Frontend EC2 :5001  (Flask UI)
  ├──► Frontend EC2 :8090  (Thymeleaf UI)
  │
  │    Frontend EC2
  │    ├── todoui-flask      ──► Backend EC2 :8080 (Spring Boot REST API)
  │    ├── todoui-thymeleaf  ──► Backend EC2 :8080 (Spring Boot REST API)
  │    ├── loadgenerator     ──► todoui-flask :5001, todoui-thymeleaf :8090
  │    └── otelcol (systemd) ──► Platform Gateway EC2 :14317 (OTLP gRPC, bypass Envoy)
  │         ▲ 수신: flask, thymeleaf (OTLP/gRPC :4317)
  │         ▲ 수집: docker_stats, hostmetrics, filelog
  │
  │    Backend EC2
  │    ├── todobackend-springboot  ──► RDS PostgreSQL :5432
  │    └── otelcol (systemd)       ──► Platform Gateway EC2 :14317
  │         ▲ 수신: springboot (OTLP/gRPC :4317)
  │         ▲ 수집: docker_stats, hostmetrics, filelog
  │
  │    RDS PostgreSQL
  │    └── mydb (port 5432, backend-sg 만 허용)
  │
  └──► Platform Gateway EC2
       └── otelcol → AMP (메트릭), OpenSearch (로그·트레이스), S3 (백업)
```

---

## OTel 리소스 속성 분류

모든 텔레메트리는 다음 리소스 속성 스키마를 따른다:

| 속성 | 값 | 적용 대상 |
|---|---|---|
| `service.namespace` | `todolist` | 전체 신호 |
| `deployment.environment` | `prod` | 전체 신호 |
| `service.name` | `flask` / `thymeleaf` / `springboot` / `host-logs` / `host-metrics` | 서비스별 |
| `source` | `app` / `container` / `host` / `sys` | 파이프라인별 |
| `host.name` | `log-platform-dev-customer-frontend` 또는 `log-platform-dev-customer-backend` | host/syslog 신호 |
| `log.source` | `syslog` | syslog 파이프라인만 |

**AMP job 라벨** (service.namespace/service.name 형식):
- `todolist/flask`
- `todolist/thymeleaf`
- `todolist/springboot`
- `todolist/host-metrics`

---

## 배포 의존 관계

1. RDS → Backend EC2 (user_data에서 rds_endpoint 필요)
2. Backend EC2 → Frontend EC2 (user_data에서 backend_private_ip 필요)
3. Platform Gateway EC2 → 고객 EC2 (customer app은 platform gateway가 먼저 떠있어야 데이터 전송 가능)
4. 각 EC2에서 otelcol (systemd) → Docker Compose 순서로 기동 (앱 컨테이너가 otelcol로 데이터 전송)

---

## RCA 에이전트 빠른 참조

| 개념 | prod (AWS EC2) 값 |
|---|---|
| `deployment.environment` | `prod` |
| `resource.service.name` (앱) | `springboot`, `thymeleaf`, `flask` |
| 호스트/syslog 로그 | `service.name=host-logs`, `source=sys` |
| 호스트 메트릭 | `service.name=host-metrics`, `source=host` |
| AMP job 라벨 | `todolist/springboot`, `todolist/flask`, `todolist/thymeleaf` |
| RDS 식별자 | `log-platform-dev-customer-postgres` |
| Frontend EC2 Name 태그 | `log-platform-dev-customer-frontend` |
| Backend EC2 Name 태그 | `log-platform-dev-customer-backend` |
| 장애 체인 | flask/thymeleaf → springboot(8080) → RDS:5432 |
| DB 에러 로그 위치 | springboot 로그 (HikariPool timeout, SEVERE 레벨) |
| infrastructure_agent | AWS RDS/EC2 직접 조회 가능 (prod 전용) |
