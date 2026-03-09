# 데이터 아키텍처 (Hot/Cold/Knowledge Base)

## 📊 현재 데이터 계층 구조

```
┌─────────────────────────────────────────────────────────────────┐
│                        데이터 수집 계층                           │
│  외부 OTel App → EC2 OTel Collector → 3개 백엔드 동시 전송      │
└─────────────────────────────────────────────────────────────────┘
                              ↓
        ┌─────────────────────┼─────────────────────┐
        ↓                     ↓                     ↓
   [메트릭]                [로그]                [트레이스]
```

---

## 1️⃣ HOT DATA (실시간 분석 - 최근 30일)

### 목적: 실시간 알람, 대시보드, AI 분석

### 메트릭 (Metrics)
- **저장소**: Amazon Managed Prometheus (AMP)
- **보존 기간**: 30일 (기본값)
- **쿼리 방식**: PromQL
- **용도**:
  - 실시간 알람 (Alert Rules)
  - Grafana 대시보드
  - Strands Agents 메트릭 조회
- **데이터 예시**:
  ```promql
  jvm_cpu_recent_utilization_ratio{service_name="api-server"}
  http_server_request_duration_seconds_bucket
  ```

### 로그 (Logs)
- **저장소**: OpenSearch Domain (VPC 내부)
- **보존 기간**: 30일 (ISM 정책)
- **인덱스**: `logs-YYYY.MM.DD`
- **쿼리 방식**: OpenSearch DSL
- **용도**:
  - 에러 로그 검색
  - Strands Agents 로그 조회
  - 실시간 디버깅
- **데이터 예시**:
  ```json
  {
    "time": "2026-03-09T10:30:00Z",
    "serviceName": "api-server",
    "severityText": "ERROR",
    "body": "OutOfMemoryError: Java heap space"
  }
  ```

### 트레이스 (Traces)
- **저장소**: OpenSearch Domain (VPC 내부)
- **보존 기간**: 30일 (ISM 정책)
- **인덱스**: `otel-v1-apm-span-*`, `otel-v1-apm-service-map`
- **쿼리 방식**: OpenSearch DSL
- **용도**:
  - 분산 추적
  - 느린 요청 분석
  - Strands Agents 트레이스 조회
- **데이터 예시**:
  ```json
  {
    "traceId": "abc123...",
    "spanId": "def456...",
    "serviceName": "api-server",
    "name": "GET /api/users",
    "durationInNanos": 150000000,
    "status": {"code": 0}
  }
  ```

---

## 2️⃣ COLD DATA (장기 보관 - 최대 365일)

### 목적: 규정 준수, 장기 트렌드 분석, 비용 절감

### S3 백업 (모든 데이터)
- **저장소**: S3 (3개 버킷)
  - `log-platform-dev-logs-backup-024249678948`
  - `log-platform-dev-traces-backup-024249678948`
  - `log-platform-dev-metrics-backup-024249678948`
- **보존 기간**:
  - 로그/트레이스: 30일
  - 메트릭: 365일
- **포맷**: JSON (OTel 표준)
- **용도**:
  - 규정 준수 (감사)
  - 재해 복구
  - 장기 트렌드 분석 (Athena)

### Athena 쿼리 (S3 위에서 SQL)
- **데이터 소스**: S3 백업
- **스키마 관리**: Glue Crawler (매일 새벽 2시)
- **데이터베이스**: `log_platform_dev_observability`
- **테이블**:
  - `logs` (S3 logs 버킷)
  - `traces` (S3 traces 버킷)
  - `metrics` (S3 metrics 버킷)
- **용도**:
  - 주간 리포트 (Lambda)
  - 장기 패턴 분석
  - 비용 효율적 쿼리
- **쿼리 예시**:
  ```sql
  SELECT severityText, COUNT(*) as count
  FROM logs
  WHERE time >= current_date - interval '7' day
    AND severityText = 'ERROR'
  GROUP BY severityText
  ```

---

## 3️⃣ KNOWLEDGE BASE (정적 지식 - 영구)

### 목적: 운영 지식, 대응 절차, 베스트 프랙티스

### 런북 (Runbooks)
- **저장소**: S3 + OpenSearch Serverless (AOSS)
  - S3: `log-platform-dev-runbooks-024249678948/runbooks/`
  - AOSS: `log-platform-dev-runbooks` 컬렉션
- **포맷**: Markdown (`.md`)
- **보존 기간**: 영구
- **벡터 임베딩**: Titan Embeddings v2
- **인덱스**: `bedrock-knowledge-base-default-index`
- **용도**:
  - Bedrock Agent RAG 검색
  - 장애 대응 절차 참조
  - 베스트 프랙티스 공유
- **데이터 예시**:
  ```markdown
  # JVM 메모리 누수 대응 절차
  
  ## 증상
  - JVM 메모리 사용률 85% 초과
  - GC 빈도 증가
  
  ## 즉시 조치
  1. 힙 덤프 생성: `jmap -dump:live,format=b,file=heap.bin <pid>`
  2. 메모리 분석: MAT 또는 VisualVM 사용
  
  ## 근본 원인
  - 캐시 무제한 증가
  - 커넥션 풀 누수
  ```

### 자동 동기화
- **트리거**: S3 `runbooks/` 업로드 → EventBridge → Lambda
- **Lambda**: `runbooks_aws.indexing_handler`
- **동작**: Bedrock Knowledge Base 동기화 (벡터 임베딩 재생성)

---

## 4️⃣ AI 메모리 (동적 지식 - 영구)

### 목적: 장애 패턴 학습, 유사 장애 검색

### 장애 이력 (Incident Memory)
- **저장소**: OpenSearch Serverless (AOSS)
  - 컬렉션: `log-platform-dev-incident-memory`
  - 인덱스: `incident-memory-index`
- **보존 기간**: 영구
- **벡터 임베딩**: Titan Embeddings v2 (에러 메시지)
- **용도**:
  - 과거 장애 패턴 검색
  - 유사 에러 매칭
  - 통계 분석 (발생 횟수, 평균 해결 시간)
- **데이터 예시**:
  ```json
  {
    "incident_id": "HighJvmCpu_2026-03-09T10:30:00Z",
    "alert_name": "HighJvmCpu",
    "status": "resolved",
    "root_cause": "메모리 누수, GC 과부하",
    "resolution": "힙 덤프 분석 후 캐시 크기 제한",
    "resolution_time_minutes": 15,
    "log_pattern_vector": [0.123, 0.456, ...],  // 1024차원
    "error_messages": ["OutOfMemoryError"]
  }
  ```

### Bedrock Session Memory (단기)
- **저장소**: Bedrock 관리형
- **보존 기간**: 30일
- **용도**: 세션 내 대화 컨텍스트
- **자동 관리**: Bedrock이 요약 및 저장

---

## 📈 데이터 흐름 다이어그램

```
┌─────────────────────────────────────────────────────────────────┐
│                         실시간 데이터 (HOT)                       │
├─────────────────────────────────────────────────────────────────┤
│  AMP (메트릭, 30일)                                              │
│  ├─ Alert Rules → SNS → Lambda → AI 분석                        │
│  └─ Grafana 대시보드                                             │
│                                                                   │
│  OpenSearch (로그/트레이스, 30일)                                │
│  ├─ Strands Agents 쿼리                                          │
│  └─ 실시간 검색                                                  │
└─────────────────────────────────────────────────────────────────┘
                              ↓ (자동 백업)
┌─────────────────────────────────────────────────────────────────┐
│                        장기 보관 (COLD)                          │
├─────────────────────────────────────────────────────────────────┤
│  S3 (로그 30일, 트레이스 30일, 메트릭 365일)                     │
│  └─ Athena (SQL 쿼리) → 주간 리포트                              │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                      정적 지식 (KNOWLEDGE BASE)                  │
├─────────────────────────────────────────────────────────────────┤
│  S3 Runbooks (Markdown)                                          │
│  └─ AOSS (벡터 검색) → Bedrock Agent RAG                         │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                      동적 지식 (AI MEMORY)                       │
├─────────────────────────────────────────────────────────────────┤
│  AOSS Incident Memory (벡터 검색)                                │
│  ├─ 장애 패턴 학습                                               │
│  └─ 유사 장애 검색                                               │
│                                                                   │
│  Bedrock Session Memory (30일)                                   │
│  └─ 대화 컨텍스트                                                │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🔍 데이터 접근 패턴

### AI 분석 시 (알람 발생)
```
1. HOT: Strands Agents → AMP/OpenSearch (최근 1시간 데이터)
2. AI MEMORY: AOSS Incident Memory (과거 장애 패턴)
3. KNOWLEDGE BASE: AOSS Runbooks (대응 절차)
4. 결과 → Slack + AOSS Incident Memory 저장
```

### 주간 리포트 생성 시
```
1. COLD: Athena → S3 (지난 7일 데이터)
2. SQL 집계 (에러 수, 느린 요청 등)
3. 결과 → Slack
```

### 운영자 수동 조회 시
```
1. HOT: Grafana → AMP (실시간 메트릭)
2. HOT: OpenSearch Dashboard → OpenSearch (로그/트레이스)
3. COLD: Athena Workbench → S3 (장기 분석)
```

---

## 💰 비용 최적화 전략

### HOT (고비용, 고성능)
- AMP: 쿼리 기반 과금 (실시간 알람 필수)
- OpenSearch: 인스턴스 기반 과금 (t3.small, 1노드)
- 30일 후 자동 삭제 → S3로 이동

### COLD (저비용, 저성능)
- S3: 스토리지 과금 (매우 저렴)
- Athena: 스캔 데이터량 기반 과금 (주 1회 실행)
- Lifecycle 정책으로 자동 삭제

### KNOWLEDGE BASE (중비용, 중성능)
- AOSS: OCU 기반 과금 (벡터 검색 필수)
- 런북은 소량 데이터 (수십 MB)
- 영구 보관 (자주 변경되지 않음)

### AI MEMORY (중비용, 중성능)
- AOSS: OCU 기반 과금 (벡터 검색 필수)
- 장애 이력은 점진적 증가 (월 수백 건)
- 영구 보관 (학습 데이터)

---

## 🎯 각 계층의 역할 요약

| 계층 | 저장소 | 보존 기간 | 주요 용도 | 쿼리 방식 |
|------|--------|-----------|-----------|-----------|
| **HOT** | AMP, OpenSearch | 30일 | 실시간 알람, AI 분석 | PromQL, DSL |
| **COLD** | S3 + Athena | 30~365일 | 장기 분석, 규정 준수 | SQL |
| **KNOWLEDGE BASE** | S3 + AOSS | 영구 | 대응 절차, RAG | 벡터 검색 |
| **AI MEMORY** | AOSS | 영구 | 장애 패턴 학습 | 벡터 검색 |

---

## 🚀 향후 개선 방향

### 1. Tiered Storage (계층형 스토리지)
```
HOT (0-7일) → WARM (8-30일) → COLD (31-365일) → GLACIER (365일+)
```
- OpenSearch Hot/Warm 노드 분리
- S3 Intelligent-Tiering 활용

### 2. 데이터 압축
- S3 Parquet 포맷 전환 (JSON → Parquet)
- Athena 쿼리 성능 10배 향상
- 스토리지 비용 50% 절감

### 3. 실시간 집계
- OpenSearch Rollup (시간별 → 일별 집계)
- AMP Recording Rules (사전 집계)
- 쿼리 성능 향상

### 4. 지능형 데이터 보존
- 중요 장애만 영구 보관
- 일반 로그는 30일 후 삭제
- AI가 중요도 판단
