# 메모리 전환 전략 (Memory Transition Strategy)

## 개요

AI 에이전트의 메모리는 **단기 메모리**와 **장기 메모리**로 구성되며, 각각의 역할과 전환 시점이 명확히 정의되어 있습니다.

---

## 1️⃣ 단기 메모리 (Short-term Memory)

### 기술: Bedrock Agent Session Memory
- **저장 위치**: AWS Bedrock 관리형 스토리지
- **보존 기간**: 30일 (자동 만료)
- **저장 방식**: 세션별 대화 요약 (SESSION_SUMMARY)
- **관리 주체**: Bedrock이 자동 관리

### 용도
- 동일 세션 내 대화 컨텍스트 유지
- 사용자와의 연속된 대화 흐름 추적
- 임시 분석 결과 캐싱

### 제한사항
- 세션 ID 기반 격리 (다른 알람과 공유 불가)
- 구조화된 쿼리 불가 (검색, 집계 등)
- 30일 후 자동 삭제

---

## 2️⃣ 장기 메모리 (Long-term Memory)

### 기술: OpenSearch Serverless (AOSS) + 벡터 검색
- **저장 위치**: AOSS 컬렉션 (`incident-memory-index`)
- **보존 기간**: 영구 (수동 삭제 전까지)
- **저장 방식**: 구조화된 문서 + 벡터 임베딩
- **관리 주체**: Lambda 코드에서 명시적 저장

### 용도
- 알람별 장애 패턴 학습
- 유사 장애 검색 (벡터 유사도)
- 통계 분석 (발생 횟수, 평균 해결 시간)
- 근본 원인 트렌드 분석

### 저장 데이터
```json
{
  "incident_id": "HighJvmCpu_2026-03-09T10:30:00Z",
  "alert_name": "HighJvmCpu",
  "timestamp": "2026-03-09T10:30:00Z",
  "severity": "critical",
  "status": "ongoing",  // or "resolved"
  "root_cause": "메모리 누수, GC 과부하",
  "resolution": "ongoing",  // 해결 후 업데이트
  "resolution_time_minutes": 0,  // 해결 후 업데이트
  "resolved_at": null,  // 해결 후 업데이트
  "metrics": {
    "session_id": "incident-HighJvmCpu-20260309103000",
    "is_recurring": true,
    "past_occurrences": 5
  },
  "log_pattern_vector": [0.123, 0.456, ...],  // 1024차원 임베딩
  "error_messages": ["OutOfMemoryError", "GC overhead limit exceeded"],
  "tags": ["HighJvmCpu", "critical"]
}
```

---

## 3️⃣ 메모리 전환 전략

### 전략: 즉시 저장 + 해결 후 업데이트 (Immediate Save + Resolution Update)

#### Phase 1: 알람 발생 (ALARM 상태)
```
SNS (ALARM) → Lambda → Bedrock Agent 분석 → AOSS 저장 (status: ongoing)
```

**저장 시점**: Agent 분석 완료 직후  
**저장 내용**:
- 초기 분석 결과 (가능한 원인, 즉시 조치)
- 에러 메시지 벡터 임베딩
- 메트릭 스냅샷
- `status: "ongoing"`
- `resolution: "ongoing"`
- `resolution_time_minutes: 0`

**이유**:
- 다음 알람 발생 시 즉시 참조 가능
- 미해결 장애도 추적 가능
- 실시간 패턴 학습

#### Phase 2: 알람 해결 (OK 상태)
```
SNS (OK) → Lambda → AOSS 업데이트 (status: resolved)
```

**업데이트 시점**: OK 상태 수신 시  
**업데이트 내용**:
- `status: "ongoing" → "resolved"`
- `resolution: "자동 복구"` (또는 실제 해결 방법)
- `resolution_time_minutes: 계산된 시간`
- `resolved_at: 현재 시각`

**이유**:
- 정확한 해결 시간 측정
- 통계 집계 시 resolved만 사용
- 해결 패턴 학습

---

## 4️⃣ 메모리 활용 시나리오

### 시나리오 1: 신규 장애
```
1. ALARM 발생 → AOSS 조회 (결과: 0건)
2. Agent: "신규 장애 패턴입니다"
3. AOSS 저장 (status: ongoing)
4. OK 수신 → AOSS 업데이트 (status: resolved, 15분 소요)
```

### 시나리오 2: 재발 장애
```
1. ALARM 발생 → AOSS 조회 (결과: 5건, 평균 12분 소요)
2. Agent: "과거 5회 발생, 평균 12분 소요. 가장 흔한 원인: 메모리 누수"
3. AOSS 저장 (status: ongoing, 6번째 발생)
4. OK 수신 → AOSS 업데이트 (status: resolved, 10분 소요)
```

### 시나리오 3: 유사 장애 검색
```
1. ALARM 발생 (에러: "OutOfMemoryError")
2. 벡터 검색 → 유사 에러 패턴 3건 발견
3. Agent: "유사 장애에서 메모리 누수가 원인이었습니다"
4. 빠른 해결 (과거 경험 활용)
```

---

## 5️⃣ 통계 집계 전략

### 집계 대상: resolved 상태만
```python
query = {
  "query": {
    "bool": {
      "must": [
        {"term": {"alert_name": "HighJvmCpu"}},
        {"term": {"status": "resolved"}}  # 해결된 장애만
      ]
    }
  },
  "aggs": {
    "avg_resolution_time": {"avg": {"field": "resolution_time_minutes"}},
    "top_root_causes": {"terms": {"field": "root_cause.keyword"}}
  }
}
```

**이유**:
- ongoing 장애는 해결 시간이 0이므로 통계 왜곡
- 실제 해결된 장애만 학습 데이터로 사용
- 평균 해결 시간의 정확도 향상

---

## 6️⃣ 대안 전략 (미채택)

### 전략 B: 해결 후에만 저장
- **장점**: 데이터 정확도 높음
- **단점**: 다음 알람 발생 시 참조 불가 (학습 지연)

### 전략 C: 주기적 요약
- **장점**: 세션 메모리 활용
- **단점**: 복잡도 증가, 실시간성 저하

---

## 7️⃣ 구현 상태

### ✅ 완료
- AOSS 인덱스 스키마 (`status`, `resolved_at` 필드 추가)
- `save_incident()` - 초기 저장
- `get_recent_ongoing_incident()` - ongoing 장애 조회
- `update_incident_resolution()` - 해결 정보 업데이트
- Lambda handler - ALARM/OK 상태 분기 처리
- Slack 알림 - 발생/해결 분리

### 🔄 테스트 필요
- 실제 알람 발생 → 저장 확인
- OK 상태 수신 → 업데이트 확인
- 재발 알람 → 과거 이력 참조 확인
- 벡터 검색 정확도 검증

---

## 8️⃣ 모니터링 포인트

1. **저장 성공률**: AOSS 403 오류 빈도
2. **업데이트 성공률**: ongoing → resolved 전환율
3. **평균 해결 시간**: 알람별 트렌드
4. **재발률**: 동일 알람 발생 빈도
5. **벡터 검색 정확도**: 유사 장애 매칭 품질

---

## 9️⃣ 향후 개선 방향

1. **자동 해결 방법 학습**
   - resolved 장애의 해결 방법을 Agent가 학습
   - 다음 발생 시 자동 제안

2. **패턴 기반 예측**
   - 특정 시간대/요일 패턴 분석
   - 사전 알림 (예: "금요일 오후 메모리 증가 패턴")

3. **근본 원인 자동 분류**
   - 에러 메시지 클러스터링
   - 원인별 해결 플레이북 자동 매칭

4. **해결 시간 예측**
   - 과거 데이터 기반 ML 모델
   - SLA 위반 사전 경고
