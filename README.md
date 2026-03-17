# AgentCore Memory 마이그레이션 진행 현황

## 배경

AOSS(OpenSearch Serverless) `incident_memory` 컬렉션을 AWS Bedrock AgentCore Memory로 교체하는 작업.
런북용 AOSS(`runbooks` 컬렉션 + Bedrock KB)는 마이그레이션 대상이 아님 — 유지.

---

## 완료된 작업

### Phase 1 — AgentCore 병렬 저장 (검증용) ✅

- **목적**: AgentCore API 동작 확인. AOSS primary 유지하면서 AgentCore에도 동일 데이터를 저장해 검증
- **변경 파일**: `agentcore_memory.py` 신규 생성
- **핵심**: 모든 예외를 조용히 처리 → 기존 AOSS 흐름에 영향 없음
- **검증 결과**: ✅ AgentCore 저장 정상 확인

---

### Phase 2 — AgentCore Primary 읽기/쓰기 ✅

- **목적**: `memory_node`(읽기)를 AgentCore primary로 전환, `memory_save_node` 추가
- **변경 파일**: `graph_agent_with_memory.py`, `bedrock_agent_runtime_handler.py`
- **흐름**:
  - `memory_node`: AgentCore 조회 → 실패 시 AOSS fallback
  - `memory_save_node`: AOSS에 ongoing 인시던트 있으면 스킵, 없으면 AgentCore + AOSS 병렬 저장
- **검증 결과**:
  - `✅ [AgentCore] 통계: 1건, 평균 87.0분` — AgentCore primary 읽기 확인
  - `📡 소스: AgentCore` — primary 소스 확인
  - `⏭️ 장기 메모리 저장 스킵 - 이미 ongoing 인시던트 존재` — 중복 저장 방지 확인

---

### Phase 3 — OK/RESOLVED 처리 AgentCore Primary 전환 ✅

- **목적**: 장애 해결(RESOLVED) 시 ongoing 조회를 AgentCore에서 먼저 시도
- **변경 파일**: `bedrock_agent_runtime_handler.py`, `agentcore_memory.py`
- **추가된 메서드**: `get_recent_ongoing_incident()`, `update_incident_resolution()`
- **특이사항**: AgentCore는 UPDATE API 없음 → resolved 레코드를 `/incidents/resolved/` 네임스페이스에 신규 저장

---

### AgentCore ongoing 검색 신뢰도 개선 ✅

- **문제**: `get_recent_ongoing_incident()`가 벡터 유사도 검색이라 `status=ongoing` 필터링이 불안정
- **개선 내용** (`agentcore_memory.py`):
  - 검색 쿼리를 저장 포맷에 맞게 변경: `"Alert: {alert_name} Status: ongoing"`
  - topK: 20 → 100
  - 1차 실패 시 `"Alert: {alert_name}"` 전체 조회 후 Python 필터(2차)
  - `_extract_ongoing()` 헬퍼 메서드 분리
- **한계**: AgentCore Memory는 내부적으로 텍스트를 재처리(summarize)하기 때문에 정확한 status 필터링이 근본적으로 불안정. ongoing 추적은 AOSS가 계속 담당하는 것이 맞음

---

### OpenSearch IAM 유저 role mapping 추가

- **문제**: `chlrudtjs` IAM 유저가 OpenSearch 쿼리 시 401
- **원인**: FGAC(Fine-Grained Access Control) 활성화 상태에서 IAM `AdministratorAccess`만으론 불충분. OpenSearch 내부 role mapping에 등록 필요
- **조치**: EC2에서 curl로 `all_access` role mapping에 ARN 추가
  ```bash
  backend_roles: [
    "arn:aws:iam::024249678948:role/log-platform-dev-otel-collector-role",
    "arn:aws:iam::024249678948:user/chlrudtjs"
  ]
  ```
- **미완료**: `user_data.sh`에 코드로 추가 필요 (EC2 재프로비저닝 시 유지되도록)

---

## 현재 상태 — 각 기능 담당

| 기능 | 담당 | 비고 |
|------|------|------|
| 과거 이력/통계 조회 | AgentCore | primary |
| 유사 장애 검색 | AgentCore | primary |
| ongoing 상태 추적 | AOSS incident_memory | AgentCore 한계로 유지 |
| 장애 저장 (ALARM) | AgentCore + AOSS | 병렬 |
| 해결 저장 (RESOLVED) | AgentCore + AOSS | 병렬 |
| 런북 검색 | Bedrock KB + AOSS runbooks | 마이그레이션 대상 아님 |

---

## 다음 작업

### 1. `user_data.sh` role mapping 코드 반영 (소규모)

EC2 재프로비저닝 시 `chlrudtjs` role mapping이 사라지지 않도록 코드에 추가.

```bash
# user_data.sh 481번째 줄 수정
-d "{\"backend_roles\":[\"$OTEL_COLLECTOR_ROLE_ARN\",\"arn:aws:iam::024249678948:user/chlrudtjs\"],\"hosts\":[],\"users\":[\"$OPENSEARCH_USER\"]}"
```

---

### 2. Phase 4 — AOSS incident_memory 제거 (대규모, 선택적)

**목적**: ongoing 추적을 DynamoDB로 이전 후 AOSS `incident_memory` 컬렉션 제거 → 비용 절감

**전제 조건**: ongoing 추적 기능이 DynamoDB로 안정적으로 이전되어야 함

**작업 순서**:

1. **DynamoDB 테이블 생성** (Terraform)
   - PK: `alert_name`, SK: `timestamp`
   - TTL 설정 (자동 만료)

2. **코드 교체**
   - `incident_memory.py`의 `get_recent_ongoing_incident()` → DynamoDB 조회로 교체
   - `save_incident()` (ongoing) → DynamoDB put_item 추가
   - `update_incident_resolution()` → DynamoDB update_item으로 교체

3. **`incident_memory.py` 제거**
   - AOSS 관련 코드 전체 삭제
   - Lambda 환경변수에서 AOSS endpoint 제거

4. **Terraform에서 AOSS `incident_memory` 컬렉션 제거**
   - `bedrock_agent_memory.tf`에서 관련 리소스 삭제

**비용 효과**: AOSS OCU 고정 비용 제거 → DynamoDB on-demand 과금으로 대폭 절감
