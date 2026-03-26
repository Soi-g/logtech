# Application Topology: 온프레미스 / 로컬 VM (dev 환경)

## Overview

로컬 VMware VM 위에서 Docker Compose로 실행되는 dev 환경 스택이다.
모든 앱 컨테이너는 단일 Docker 브리지 네트워크(`todonet`)를 공유하고,
OTel Collector 컨테이너(`otelcol`)가 텔레메트리를 수집해 플랫폼 게이트웨이 EC2로 전송한다.

- **서비스 네임스페이스**: `todolist`
- **배포 환경 (텔레메트리)**: `dev` (compose + collector 리소스 프로세서에서 고정)
- **오케스트레이션**: Docker Compose (`LFS148-code_v2/docker-compose.yaml`)
- **데이터베이스**: PostgreSQL 16 컨테이너 (`postgresdb`) — RDS 아님

---

## 네트워크 토폴로지

### Docker 네트워크

| 이름 | 드라이버 | 용도 |
|---|---|---|
| `todonet` | bridge (기본) | 모든 서비스 공유; DNS 이름 = Compose 서비스 이름 |

### VM 호스트에 바인딩된 포트

| 호스트 포트 | 서비스 | 컨테이너 포트 | 용도 |
|---|---|---|---|
| 5432 | `postgresdb` | 5432 | PostgreSQL (외부 접근 선택적) |
| 8080 | `todobackend-springboot` | 8080 | Spring Boot REST API |
| 8090 | `todoui-thymeleaf` | 8090 | Thymeleaf UI |
| 5001 | `todoui-flask` | 5000 | Flask UI |
| 4317 | `otelcol` | 4317 | OTLP gRPC (앱 → otelcol) |
| 4318 | `otelcol` | 4318 | OTLP HTTP |

### 컨테이너 간 통신 (East-West, todonet 내부)

| 출발 | 목적지 | 경로 |
|---|---|---|
| flask → | springboot | `BACKEND_URL=http://todobackend-springboot:8080/todos/` |
| thymeleaf → | springboot | `BACKEND_URL=http://todobackend-springboot:8080/` |
| springboot → | postgresdb | JDBC `postgresdb:5432` / DB `mydb` |
| flask, thymeleaf, springboot → | otelcol | `OTEL_EXPORTER_OTLP_ENDPOINT` (.env에서 주입) → `http://otelcol:4317` |
| otelcol → | Platform Gateway EC2 | `endpoint` (.env `OTEL_EXPORTER_OTLP_ENDPOINT_EXTERNAL` 등) |

---

## 데이터 계층

### `postgresdb`

| 항목 | 값 |
|---|---|
| 이미지 | `postgres:16.3` (.env `POSTGRES_IMAGE`에서 설정) |
| DB | `mydb` |
| 사용자 | `matthias` |
| 비밀번호 | `password` |
| 볼륨 | 정의 없음 (ephemeral — 컨테이너 재시작 시 데이터 소멸) |
| 비고 | prod의 RDS와 달리 컨테이너로 실행; 중지 시 springboot HikariPool 연결 실패 |

---

## 애플리케이션 계층

### `todobackend-springboot`

| 항목 | 값 |
|---|---|
| 이미지 | `ghcr.io/lftraining/lfs148-code-todobackend-springboot:v2404` (로컬 빌드도 가능) |
| 포트 | 8080(host) → 8080(container) |
| SPRING_PROFILES_ACTIVE | `prod` → `application-prod.properties` 사용 (postgresdb:5432 연결) |
| OTel endpoint | `${OTEL_EXPORTER_OTLP_ENDPOINT}` (.env) → todonet 내 `http://otelcol:4317` |
| service.name | `springboot` |
| deployment.environment | `dev` |
| 특이사항 | DB 연결 에러(HikariPool timeout)는 이 서비스 로그에 SEVERE 레벨로 기록됨 |

### `todoui-thymeleaf`

| 항목 | 값 |
|---|---|
| 이미지 | `ghcr.io/lftraining/lfs148-code-todoui-thymeleaf:v2404` |
| 포트 | 8090(host) → 8090(container) |
| BACKEND_URL | `http://todobackend-springboot:8080/` |
| OTel endpoint | `${OTEL_EXPORTER_OTLP_ENDPOINT}` |
| service.name | `thymeleaf` |
| deployment.environment | `dev` |

### `todoui-flask`

| 항목 | 값 |
|---|---|
| 이미지 | `todoui-flask:dev` (로컬 빌드, `./todoui-flask` Dockerfile) |
| 포트 | 5001(host) → 5000(container) |
| BACKEND_URL | `http://todobackend-springboot:8080/todos/` |
| OTel endpoint | `${OTEL_EXPORTER_OTLP_ENDPOINT}` |
| service.name | `flask` |
| deployment.environment | `dev` |
| 특이사항 | `OTEL_SEMCONV_STABILITY_OPT_IN=http` 설정 (Python OTel HTTP 라벨을 Java 형식과 통일) |

### `loadgenerator`

| 항목 | 값 |
|---|---|
| 이미지 | `ghcr.io/lftraining/lfs148-code-simple-generator:v2404` |
| 역할 | todoui-thymeleaf:8090, todoui-flask:5000 대상 합성 트래픽 루프 |
| 노출 포트 | 없음 |

---

## 관측성 경로

### `otelcol` (OpenTelemetry Collector Contrib 컨테이너)

| 항목 | 값 |
|---|---|
| 이미지 | `.env COLLECTOR_CONTRIB_IMAGE` |
| hostname | `k8s-master1` (compose에서 고정) |
| user | `0` (root — docker.sock 마운트 필요) |
| config | `./collector/otel-collector-config.yml` (마운트) |
| file storage | `./otelcol-data` (마운트) |

**볼륨 마운트:**

| 호스트 경로 | 컨테이너 경로 | 목적 |
|---|---|---|
| `./collector/otel-collector-config.yml` | `/etc/otel-collector-config.yml` | Collector 설정 |
| `/var/run/docker.sock` | `/var/run/docker.sock` | docker_stats 리시버 |
| `/` | `/host:ro` | hostmetrics 루트 경로 |
| `/sys` | `/host/sys:ro` | hostmetrics |
| `/proc` | `/host/proc:ro` | hostmetrics |

**리시버:**

| 리시버 | 엔드포인트/소스 | 목적 |
|---|---|---|
| otlp (gRPC) | `0.0.0.0:4317` | springboot, thymeleaf, flask OTLP 수신 |
| docker_stats | `unix:///var/run/docker.sock` | 컨테이너 메트릭 |
| hostmetrics | `root_path: /host` | VM CPU/메모리/디스크/네트워크/파일시스템 |
| filelog | `/host/var/log/syslog`, `/host/var/log/auth.log` | VM 호스트 OS 로그 |

**파이프라인:**

| 파이프라인 | 리시버 | 프로세서 | source 라벨 |
|---|---|---|---|
| traces | otlp | resource/app | app |
| metrics/app | otlp | resource/app | app |
| metrics/container | docker_stats | resource/container | container |
| metrics/host | hostmetrics | resource/host | host |
| logs/app | otlp | resource/app | app |
| logs/host | filelog | resource/syslog | sys |

**엑스포터:**
- `otlp/gateway`: 플랫폼 게이트웨이 EC2로 전송 (endpoint는 `.env`에서 설정)
- `debug`: 기본 verbosity (로컬 디버그용)

### 환경변수 설정 (`.env`)

| 변수 | 용도 |
|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | 앱 컨테이너 → otelcol 엔드포인트 (todonet 내 `http://otelcol:4317`) |
| `POSTGRES_IMAGE` | postgresdb 이미지 (기본 `postgres:16.3`) |
| `COLLECTOR_CONTRIB_IMAGE` | otelcol 이미지 |

---

## 서비스 통신 맵

```
외부 접근 (VM 호스트)
  ├──► :5001  (Flask UI)
  ├──► :8090  (Thymeleaf UI)
  ├──► :8080  (Spring Boot REST API)
  └──► :5432  (PostgreSQL, 선택적)

Docker todonet 내부
  todoui-flask        ──► todobackend-springboot:8080/todos/
  todoui-thymeleaf    ──► todobackend-springboot:8080/
  todobackend-springboot ──► postgresdb:5432
  loadgenerator       ──► todoui-thymeleaf:8090, todoui-flask:5000

  [모든 앱 컨테이너] ──► otelcol:4317 (OTLP gRPC)

  otelcol ──► Platform Gateway EC2 (gRPC)
               └── → AMP (메트릭)
               └── → OpenSearch (로그·트레이스)
```

---

## OTel 리소스 속성 분류

| 속성 | 값 | 적용 대상 |
|---|---|---|
| `service.namespace` | `todolist` | 전체 신호 |
| `deployment.environment` | `dev` | 전체 신호 |
| `service.name` | `springboot` / `thymeleaf` / `flask` / `host-logs` | 서비스별 |
| `source` | `app` / `container` / `host` / `sys` | 파이프라인별 |

**OpenSearch 인덱스 (플랫폼 공유):**
- `logs-app`: 앱 OTLP 로그 — `resource.deployment.environment.keyword = "dev"` 로 필터
- `traces-app`: 분산 트레이스

**AMP job 라벨** (service.namespace/service.name 형식):
- `todolist/flask`
- `todolist/thymeleaf`
- `todolist/springboot`

---

## RCA 에이전트 빠른 참조

| 개념 | dev (VM/Docker) 값 |
|---|---|
| `deployment.environment` | `dev` |
| `resource.service.name` (앱) | `springboot`, `thymeleaf`, `flask` |
| 호스트/syslog 로그 | `service.name=host-logs`, `source=sys` |
| AMP job 라벨 | `todolist/springboot`, `todolist/flask`, `todolist/thymeleaf` |
| DB | `postgresdb` 컨테이너 (포트 5432) — RDS 아님 |
| 장애 체인 | flask/thymeleaf → springboot(8080) → postgresdb:5432 |
| DB 에러 로그 위치 | **springboot** 로그 (HikariPool timeout, SEVERE 레벨) — flask 로그 아님 |
| flask 5xx 원인 | flask 자체 에러보다 springboot 500 응답인 경우가 대부분 |
| infrastructure_agent | **사용 불가** — AWS RDS/EC2 전용 도구; 로컬 Docker 컨테이너 상태 조회 불가 |
| 분석 한계 | postgresdb 컨테이너 중지 여부를 API로 확인하는 수단 없음; springboot SEVERE 로그로 간접 확인 |
