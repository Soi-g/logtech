# AI 시스템 상세 동작 흐름

## 🎯 핵심 아키텍처

```
SNS 알람 → Lambda → [Strands Agents는 사용 안 함] → Bedrock Agent Runtime → Slack
                              ↓
                        AOSS 메모리 저장
```

## ⚠️ 중요 발견: Strands Agents는 실제로 사용되지 않음!

코드를 정확히 분석한 결과, **Strands Agents는 임포트만 되어 있고 실제로 호출되지 않습니다.**

### 증거:
```python
# bedrock_agent_runtime_handler.py

# 임포트는 되어 있음
from agents_aws import metrics_agent, logs_agent, traces_agent

# collect_observability_data() 함수는 정의되어 있지만...
def collect_observability_data(question: str) -> dict:
    """Strands Agents를 사용하여 메트릭/로그/트레이스 수집"""
    # ... Strands Agents 호출 코드 ...

# ❌ 이 함수는 어디서도 호출되지 않음!
# invoke_agent()에서도 호출 안 함
# lambda_handler()에서도 호출 안 함
```

---

## 📋 실제 AI 동작 흐름 (단계별)

### Phase 1: 알람 발생 (ALARM 상태)

#### Step 1: SNS 트리거
```
AMP Alert Rules → Alertmanager → SNS Topic → Lambda
```

**SNS 메시지 예시:**
```json
{
  "AlarmName": "HighJvmCpu",
  "AlarmDescription": "JVM CPU 사용률 80% 초과",
  "NewStateValue": "ALARM",
  "StateChangeTime": "2026-03-09T10:30:00.000Z"
}
```

#### Step 2: Lambda Handler 시작
```python
def lambda_handler(event, context):
    # SNS 메시지 파싱
    alert_name = "HighJvmCpu"
    alert_description = "JVM CPU 사용률 80% 초과"
    new_state = "ALARM"
```

#### Step 3: 과거 이력 조회 (AOSS)
```python
memory = IncidentMemory()

# 3-1. 통계 조회 (resolved 상태만)
stats = memory.get_stats(alert_name)
# 결과: {'count': 5, 'is_new': False, 'avg_resolution_time': 12.5, 'most_common_cause': '메모리 누수'}

# 3-2. 유사 장애 검색 (벡터 검색)
similar = memory.search_similar_incidents(alert_name, limit=3)
# 결과: [
#   {'incident_id': '...', 'root_cause': '메모리 누수', 'resolution': '힙 덤프 분석', 'resolution_time_minutes': 15},
#   {'incident_id': '...', 'root_cause': 'GC 과부하', 'resolution': 'GC 튜닝', 'resolution_time_minutes': 10},
#   ...
# ]
```

**AOSS 쿼리 (통계):**
```json
{
  "query": {
    "bool": {
      "must": [
        {"term": {"alert_name": "HighJvmCpu"}},
        {"term": {"status": "resolved"}}
      ]
    }
  },
  "aggs": {
    "total_count": {"value_count": {"field": "incident_id"}},
    "avg_resolution_time": {"avg": {"field": "resolution_time_minutes"}},
    "top_root_causes": {"terms": {"field": "root_cause.keyword", "size": 1}}
  }
}
```

#### Step 4: 컨텍스트 구성
```python
context = f"""
🚨 알람 발생
알람명: HighJvmCpu
심각도: high
설명: JVM CPU 사용률 80% 초과

📊 과거 장애 이력:
✅ 과거 5회 발생
📈 평균 해결 시간: 12.5분
🔍 가장 흔한 원인: 메모리 누수

최근 3건:
1. 2026-03-08T15:20:00Z
   원인: 메모리 누수
   해결: 힙 덤프 분석 후 캐시 크기 제한
   소요: 15분

2. 2026-03-07T09:10:00Z
   원인: GC 과부하
   해결: GC 튜닝 (G1GC → ZGC)
   소요: 10분

3. 2026-03-05T18:30:00Z
   원인: 메모리 누수
   해결: 커넥션 풀 누수 수정
   소요: 12분

📋 분석 요청:
1. 현재 메트릭, 로그, 트레이스를 조회하여 근본 원인 파악
2. 과거 이력을 참조하여 빠른 해결 방법 제시
3. 즉시 조치 및 후속 조치 권장
4. 관련 런북 참조

반드시 JSON 형식으로 응답하세요:
{...}
"""
```

#### Step 5: Bedrock Agent Runtime 호출
```python
session_id = "incident-HighJvmCpu-20260309103000"

response = bedrock_agent_runtime.invoke_agent(
    agentId=AGENT_ID,  # Bedrock Agent ID
    agentAliasId=AGENT_ALIAS_ID,  # prod alias
    sessionId=session_id,
    enableTrace=True,
    inputText=context  # 위에서 구성한 컨텍스트
)
```

**Bedrock Agent가 하는 일:**
1. **컨텍스트 이해**: 알람 정보 + 과거 이력 분석
2. **Knowledge Base 검색** (자동): 런북에서 관련 대응 절차 RAG 검색
3. **Session Memory 활용**: 이전 대화 컨텍스트 참조 (30일)
4. **응답 생성**: JSON 형식으로 분석 결과 반환

**중요: Bedrock Agent는 실시간 데이터를 직접 조회하지 않음!**
- ❌ AMP 메트릭 조회 안 함
- ❌ OpenSearch 로그 조회 안 함
- ❌ 트레이스 조회 안 함
- ✅ Lambda가 제공한 컨텍스트만 사용
- ✅ Knowledge Base (런북) 검색만 수행

#### Step 6: 응답 스트림 처리
```python
result_text = ""
trace_data = []

for event in response['completion']:
    if 'chunk' in event:
        chunk = event['chunk']
        if 'bytes' in chunk:
            result_text += chunk['bytes'].decode('utf-8')
    
    if 'trace' in event:
        trace_data.append(event['trace'])
```

**Agent 응답 예시:**
```json
{
  "incident_summary": "JVM CPU 사용률 80% 초과, 메모리 누수 의심",
  "is_recurring": true,
  "past_occurrences": 5,
  "likely_root_causes": ["메모리 누수", "GC 과부하", "무한 루프"],
  "severity": "critical",
  "impact": "API 응답 지연, 사용자 경험 저하",
  "immediate_actions": [
    "힙 덤프 생성: jmap -dump:live,format=b,file=heap.bin <pid>",
    "스레드 덤프 생성: jstack <pid> > threads.txt",
    "CPU 프로파일링 시작"
  ],
  "follow_up_actions": [
    "MAT로 힙 덤프 분석",
    "메모리 누수 원인 코드 수정",
    "캐시 크기 제한 설정"
  ],
  "evidence_summary": [
    "과거 5회 발생, 평균 12.5분 소요",
    "가장 흔한 원인: 메모리 누수",
    "최근 3건 모두 힙 덤프 분석으로 해결"
  ],
  "runbook_references": [
    {
      "source": "jvm-memory-leak.md",
      "section": "진단 및 대응",
      "relevance": "메모리 누수 대응 절차"
    }
  ]
}
```

#### Step 7: JSON 파싱
```python
# 마크다운 코드 블록 제거
if '```json' in result_text:
    cleaned_text = result_text.split('```json')[1].split('```')[0].strip()

# JSON 객체 추출
if '{' in cleaned_text and '}' in cleaned_text:
    start_idx = cleaned_text.find('{')
    end_idx = cleaned_text.rfind('}') + 1
    cleaned_text = cleaned_text[start_idx:end_idx]

# 파싱
report = json.loads(cleaned_text)
```

#### Step 8: Slack 전송
```python
send_to_slack(alert_name, result)
```

**Slack 메시지:**
```
🚨 HighJvmCpu

심각도: critical
과거 이력: 🔄 재발 (5회 발생, 평균 12분 소요)

📝 요약
JVM CPU 사용률 80% 초과, 메모리 누수 의심

🔍 가능한 원인
• 메모리 누수
• GC 과부하
• 무한 루프

⚡ 즉시 조치
1. 힙 덤프 생성: jmap -dump:live,format=b,file=heap.bin <pid>
2. 스레드 덤프 생성: jstack <pid> > threads.txt
3. CPU 프로파일링 시작

📖 관련 런북
• [jvm-memory-leak.md] 진단 및 대응

Session: incident-HighJvmCpu-20260309103000
```

#### Step 9: 장기 메모리 저장 (AOSS)
```python
incident_data = {
    'alert_name': 'HighJvmCpu',
    'severity': 'critical',
    'root_cause': '메모리 누수, GC 과부하, 무한 루프',  # Agent 분석 결과
    'resolution': 'ongoing',  # 아직 미해결
    'resolution_time': 0,
    'metrics': {
        'session_id': 'incident-HighJvmCpu-20260309103000',
        'is_recurring': True,
        'past_occurrences': 5
    },
    'error_messages': [
        '과거 5회 발생, 평균 12.5분 소요',
        '가장 흔한 원인: 메모리 누수',
        '최근 3건 모두 힙 덤프 분석으로 해결'
    ],
    'state_change_time': '2026-03-09T10:30:00.000Z',
    'status': 'ongoing'
}

memory.save_incident(incident_data)
```

**AOSS 저장 (벡터 임베딩):**
```python
# 에러 메시지를 Titan Embeddings로 벡터화
embedding = bedrock_runtime.invoke_model(
    modelId='amazon.titan-embed-text-v2:0',
    body=json.dumps({"inputText": ' '.join(error_messages)})
)
# 결과: [0.123, 0.456, ..., 0.789]  # 1024차원 벡터

doc = {
    'incident_id': 'HighJvmCpu_2026-03-09T10:30:00Z',
    'alert_name': 'HighJvmCpu',
    'timestamp': '2026-03-09T10:30:00.000Z',
    'severity': 'critical',
    'root_cause': '메모리 누수, GC 과부하, 무한 루프',
    'resolution': 'ongoing',
    'resolution_time_minutes': 0,
    'metrics': {...},
    'log_pattern_vector': [0.123, 0.456, ..., 0.789],  # 벡터 임베딩
    'error_messages': [...],
    'tags': ['HighJvmCpu', 'critical'],
    'status': 'ongoing'
}

# AOSS에 저장
_aoss_request('POST', '/incident-memory-index/_doc', doc)
```

---

### Phase 2: 알람 해결 (OK 상태)

#### Step 1: SNS 트리거
```
AMP Alert Rules → Alertmanager → SNS Topic → Lambda
```

**SNS 메시지:**
```json
{
  "AlarmName": "HighJvmCpu",
  "NewStateValue": "OK",
  "StateChangeTime": "2026-03-09T10:45:00.000Z"
}
```

#### Step 2: Ongoing 장애 조회
```python
recent_incident = memory.get_recent_ongoing_incident(alert_name)
# 결과: {
#   'incident_id': 'HighJvmCpu_2026-03-09T10:30:00Z',
#   'timestamp': '2026-03-09T10:30:00.000Z',
#   ...
# }
```

#### Step 3: 해결 시간 계산
```python
start_time = datetime.fromisoformat('2026-03-09T10:30:00+00:00')
end_time = datetime.fromisoformat('2026-03-09T10:45:00+00:00')
resolution_minutes = (end_time - start_time).total_seconds() / 60
# 결과: 15.0분
```

#### Step 4: AOSS 업데이트
```python
memory.update_incident_resolution(
    incident_id='HighJvmCpu_2026-03-09T10:30:00Z',
    resolution='자동 복구',
    resolution_time_minutes=15.0
)
```

**AOSS 업데이트 쿼리:**
```json
{
  "doc": {
    "resolution": "자동 복구",
    "resolution_time_minutes": 15.0,
    "status": "resolved",
    "resolved_at": "2026-03-09T10:45:00.000Z"
  }
}
```

#### Step 5: Slack 알림
```
✅ HighJvmCpu 해결

소요 시간: 15.0분
상태: 정상 복구

장기 메모리에 해결 정보가 저장되었습니다.
```

---

## 🔍 AI 컴포넌트 역할 정리

### 1. Bedrock Agent Runtime (Claude 3.5 Sonnet v2)
**역할**: 장애 분석 및 대응 방안 생성

**입력**:
- 알람 정보 (이름, 설명, 심각도)
- 과거 장애 통계 (발생 횟수, 평균 해결 시간, 흔한 원인)
- 유사 장애 이력 (최근 3건)

**처리**:
- 컨텍스트 이해 및 분석
- Knowledge Base (런북) RAG 검색
- Session Memory 참조 (30일)
- JSON 형식 응답 생성

**출력**:
- 장애 요약
- 가능한 원인 (과거 이력 기반)
- 즉시 조치 / 후속 조치
- 런북 참조

**제약**:
- ❌ 실시간 데이터 조회 불가 (AMP, OpenSearch 접근 안 함)
- ✅ Lambda가 제공한 컨텍스트만 사용

### 2. Titan Embeddings v2
**역할**: 텍스트 → 벡터 변환 (유사도 검색용)

**입력**: 에러 메시지 텍스트
```
"OutOfMemoryError: Java heap space"
```

**출력**: 1024차원 벡터
```
[0.123, 0.456, 0.789, ..., 0.321]
```

**용도**:
- 유사 에러 패턴 검색
- AOSS 벡터 검색 (KNN)

### 3. AOSS Incident Memory
**역할**: 장애 이력 저장 및 검색

**저장 데이터**:
- 장애 메타데이터 (알람명, 시간, 심각도)
- 분석 결과 (원인, 해결 방법)
- 벡터 임베딩 (에러 메시지)
- 상태 (ongoing / resolved)

**검색 기능**:
- 통계 집계 (발생 횟수, 평균 해결 시간)
- 벡터 유사도 검색 (유사 에러 패턴)
- 키워드 검색 (알람명)

### 4. Knowledge Base (런북)
**역할**: 대응 절차 RAG 검색

**저장 위치**: S3 + AOSS
**포맷**: Markdown → 벡터 임베딩
**검색 방식**: Bedrock Agent가 자동 RAG 검색

**예시 런북**:
```markdown
# JVM 메모리 누수 대응 절차

## 진단
1. 힙 덤프 생성
2. MAT 분석

## 해결
1. 메모리 누수 코드 수정
2. 캐시 크기 제한
```

### 5. Strands Agents (❌ 사용 안 함)
**현재 상태**: 코드에 정의되어 있지만 호출되지 않음

**원래 의도**:
- `metrics_agent`: AMP 메트릭 조회
- `logs_agent`: OpenSearch 로그 조회
- `traces_agent`: OpenSearch 트레이스 조회

**문제**:
- `collect_observability_data()` 함수가 호출되지 않음
- Bedrock Agent는 실시간 데이터 접근 불가

---

## 🚨 현재 시스템의 한계

### 1. 실시간 데이터 미사용
- Bedrock Agent는 과거 이력만 참조
- 현재 메트릭/로그/트레이스를 보지 못함
- Lambda가 제공한 컨텍스트만 사용

### 2. Strands Agents 미활용
- 정의는 되어 있지만 호출 안 됨
- 실시간 데이터 수집 기능 사용 안 함

### 3. 분석 정확도 제한
- 과거 패턴 기반 추론만 가능
- 현재 상황 실시간 분석 불가

---

## 💡 개선 방안

### Option 1: Strands Agents 활성화
```python
def invoke_agent(alert_name, alert_description, severity):
    # 1. 과거 이력 조회
    memory = IncidentMemory()
    stats = memory.get_stats(alert_name)
    
    # 2. 실시간 데이터 수집 (Strands Agents)
    observability_data = collect_observability_data(
        f"알람 {alert_name} 발생, 현재 상태 분석"
    )
    
    # 3. 컨텍스트 구성 (과거 이력 + 실시간 데이터)
    context = f"""
    알람: {alert_name}
    과거 이력: {stats}
    
    현재 메트릭: {observability_data['metrics']}
    현재 로그: {observability_data['logs']}
    현재 트레이스: {observability_data['traces']}
    
    분석 요청: ...
    """
    
    # 4. Bedrock Agent 호출
    response = bedrock_agent_runtime.invoke_agent(...)
```

### Option 2: Bedrock Agent에 Action Group 추가
```python
# Bedrock Agent가 직접 AMP/OpenSearch 조회
# Action Group으로 Lambda 함수 연결
# Agent가 필요 시 실시간 데이터 요청
```

### Option 3: 2단계 분석
```python
# 1단계: Strands Agents로 실시간 분석
metrics_analysis = metrics_agent("현재 메트릭 분석")
logs_analysis = logs_agent("에러 로그 검색")

# 2단계: Bedrock Agent로 종합 분석
context = f"""
과거 이력: {stats}
실시간 메트릭: {metrics_analysis}
실시간 로그: {logs_analysis}
"""
response = bedrock_agent_runtime.invoke_agent(...)
```

---

## 📊 데이터 흐름 다이어그램

```
┌─────────────────────────────────────────────────────────────┐
│                      알람 발생 (ALARM)                       │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  Lambda: bedrock_agent_runtime_handler.lambda_handler       │
│  1. SNS 메시지 파싱                                          │
│  2. AOSS 과거 이력 조회 (통계 + 유사 장애)                  │
│  3. 컨텍스트 구성 (알람 정보 + 과거 이력)                   │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  Bedrock Agent Runtime (Claude 3.5 Sonnet v2)               │
│  - 컨텍스트 분석                                             │
│  - Knowledge Base RAG 검색 (런북)                            │
│  - Session Memory 참조 (30일)                                │
│  - JSON 응답 생성                                            │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  Lambda: 응답 처리                                           │
│  1. JSON 파싱                                                │
│  2. Slack 전송                                               │
│  3. AOSS 저장 (status: ongoing)                              │
│     - Titan Embeddings로 벡터화                              │
│     - 장애 이력 문서 저장                                    │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                      알람 해결 (OK)                          │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  Lambda: 해결 처리                                           │
│  1. Ongoing 장애 조회                                        │
│  2. 해결 시간 계산                                           │
│  3. AOSS 업데이트 (status: resolved)                         │
│  4. Slack 알림                                               │
└─────────────────────────────────────────────────────────────┘
```

---

## 🎯 결론

현재 AI 시스템은:
- ✅ 과거 장애 패턴 학습 및 참조
- ✅ 런북 기반 대응 절차 제시
- ✅ 장기 메모리 (AOSS) 활용
- ❌ 실시간 데이터 분석 미지원 (Strands Agents 미사용)

**핵심 개선 포인트**: Strands Agents를 활성화하여 실시간 데이터 분석 추가!
