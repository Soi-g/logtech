# AWS Observability Platform - AI 비용 최적화 전략

## 현재 아키텍처

```
SNS 알람 → Lambda → LangGraph
                      ├─ classify_node (Haiku 3.5) 🔵
                      ├─ metrics_agent (Haiku 3.5) 🔵
                      ├─ logs_agent (Haiku 3.5) 🔵
                      ├─ traces_agent (Haiku 3.5) 🔵
                      ├─ runbook_node (벡터 검색)
                      └─ synthesize_node (Sonnet 3.5 v2) 🟢
                           → Slack
```

---

## 전략 1: 모델 계층화 ✅ 적용됨

### 개념
- **단순 작업**: Haiku 3.5 (저렴, 빠름)
- **복잡 분석**: Sonnet 3.5 v2 (고품질)

### 모델 가격 비교

| 모델 | 입력 ($/MTok) | 출력 ($/MTok) | 용도 |
|------|--------------|--------------|------|
| Haiku 3.5 | $0.8 | $4 | 분류, 데이터 수집 |
| Sonnet 3.5 v2 | $3 | $15 | 최종 종합 분석 |
| Sonnet 4 | $3 | $15 | (미사용) |

### 적용 내역

#### 1. classify_node (Haiku 3.5)
```python
classify_llm = ChatBedrockConverse(
    model="us.anthropic.claude-3-5-haiku-20241022-v1:0",
    region_name="ap-northeast-2",
)
```
- 입력: 500 토큰 (알람 정보)
- 출력: 20 토큰 ("metrics, logs, traces")
- 비용: $0.0008/호출

#### 2. Strands Agents (Haiku 3.5)
```python
haiku_model = BedrockModel(
    model_id="us.anthropic.claude-3-5-haiku-20241022-v1:0",
    region_name=AWS_REGION,
)

metrics_agent = Agent(model=haiku_model, ...)
logs_agent = Agent(model=haiku_model, ...)
traces_agent = Agent(model=haiku_model, ...)
```
- 각 Agent: 입력 1,000 토큰, 출력 500 토큰
- 비용: $0.0028/Agent
- 3개 Agent: $0.0084/호출

#### 3. synthesize_node (Sonnet 3.5 v2)
```python
synthesize_llm = ChatBedrockConverse(
    model="apac.anthropic.claude-3-5-sonnet-20241022-v2:0",
    region_name="ap-northeast-2",
)
```
- 입력: 5,000 토큰 (전체 분석 결과)
- 출력: 800 토큰 (JSON 리포트)
- 비용: $0.027/호출

### 비용 절감 효과

**이전 (모두 Sonnet 4):**
```
classify: $0.0015
3x Agents: $0.0405
synthesize: $0.027
────────────────
총: $0.069/알람
```

**현재 (모델 계층화):**
```
classify (Haiku): $0.0008
3x Agents (Haiku): $0.0084
synthesize (Sonnet 3.5 v2): $0.027
────────────────
총: $0.0362/알람
```

**절감률: 48%** 🎉

**월간 비용 (1,000 알람 기준):**
- 이전: $69/월
- 현재: $36/월
- 절감: $33/월

---

## 전략 2: 조건부 실행 (미적용)

### 개념
재발 알람 중 패턴이 명확한 경우 Strands Agents 스킵

### 조건
```python
if (
    past_occurrences >= 5 and           # 5회 이상 발생
    cause_ratio >= 0.9 and              # 90% 이상 동일 원인
    days_since_last < 7                 # 최근 7일 이내
):
    # Strands Agents 스킵
    # 과거 이력만으로 분석
```

### 예상 효과
- 60%의 알람이 조건 충족 (재발 패턴)
- LLM 호출: 5회 → 1회 (80% 감소)

**비용 (조건부 실행 추가):**
```
명확한 패턴 (60%):
  synthesize만: $0.027 × 0.6 = $0.0162

불명확한 패턴 (40%):
  전체 실행: $0.0362 × 0.4 = $0.0145

────────────────
평균: $0.0307/알람
```

**절감률: 55% (전략 1 대비 15% 추가 절감)**

---

## 전략 3: 프롬프트 캐싱 (미적용)

### 개념
정적 데이터(시스템 프롬프트, 런북)를 캐시하여 재사용

### 제약사항
- **Bedrock Agent Runtime은 캐싱 미지원**
- Bedrock Converse API로 전환 필요
- 현재 아키텍처에서는 적용 불가

### 캐시 가능 데이터
- 시스템 프롬프트: 2,000 토큰
- 알람 정의: 1,000 토큰
- 런북 요약: 5,000 토큰
- 총: 8,000 토큰

### 예상 효과 (적용 시)
```
첫 호출:
  캐시 생성: 8,000 × $3.75/MTok = $0.03
  일반 입력: 2,000 × $3/MTok = $0.006
  출력: 2,000 × $15/MTok = $0.03
  총: $0.066

후속 호출 (5분 내):
  캐시 읽기: 8,000 × $0.3/MTok = $0.0024
  일반 입력: 2,000 × $3/MTok = $0.006
  출력: 2,000 × $15/MTok = $0.03
  총: $0.0384 (42% 절감)
```

**버스트 알람 시 효과적** (5분 내 여러 알람 발생)

---

## 전략 조합 효과

### 시나리오: 월 1,000 알람

| 전략 | 알람당 비용 | 월간 비용 | 절감률 |
|------|------------|----------|--------|
| 기본 (Sonnet 4) | $0.069 | $69 | - |
| 전략 1: 모델 계층화 ✅ | $0.0362 | $36 | 48% |
| 전략 1+2: 조건부 실행 | $0.0307 | $31 | 55% |
| 전략 1+2+3: 캐싱 | $0.0234 | $23 | 66% |

---

## 구현 상태

### ✅ 완료
- [x] 전략 1: 모델 계층화
  - [x] classify_node → Haiku 3.5
  - [x] Strands Agents → Haiku 3.5
  - [x] synthesize_node → Sonnet 3.5 v2

### ⏳ 미적용
- [ ] 전략 2: 조건부 실행
  - [ ] `is_pattern_clear()` 함수 구현
  - [ ] classify_node에 조건부 로직 추가
  - [ ] CloudWatch 메트릭 추가
  
- [ ] 전략 3: 프롬프트 캐싱
  - [ ] Bedrock Converse API로 전환 (현재 Agent Runtime 사용)
  - [ ] 캐시 블록 분리
  - [ ] 캐시 워밍 구현

---

## 다음 단계

1. **전략 1 테스트** (현재 적용됨)
   - Lambda 배포 후 실제 비용 모니터링
   - CloudWatch Logs에서 모델 사용 확인

2. **전략 2 구현 고려**
   - 과거 이력 데이터 분석
   - 패턴 명확도 임계값 결정

3. **전략 3은 보류**
   - Bedrock Agent Runtime의 편의성 유지
   - 캐싱 효과가 크지 않으면 현재 구조 유지

---

## 모니터링

### CloudWatch 메트릭
```python
cloudwatch.put_metric_data(
    Namespace='ObservabilityPlatform/AI',
    MetricData=[
        {
            'MetricName': 'ModelUsage',
            'Dimensions': [
                {'Name': 'Model', 'Value': 'Haiku3.5'},
                {'Name': 'Node', 'Value': 'classify'}
            ],
            'Value': 1.0,
            'Unit': 'Count'
        }
    ]
)
```

### 비용 추적
- AWS Cost Explorer에서 Bedrock 비용 확인
- 모델별 호출 횟수 집계
- 알람당 평균 비용 계산

---

**작성일**: 2026-03-09  
**버전**: 1.0  
**상태**: 전략 1 적용 완료
