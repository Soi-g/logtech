"""
Analysis Agents - 도메인별 분석 전문가
각 도메인(메트릭/로그/트레이스)의 데이터를 깊이 분석
"""

from strands import Agent
from strands.models import BedrockModel

# ============================================================
# 모델 설정
# ============================================================

# Haiku 3.5 - 도메인별 분석 (비용 효율적)
haiku_model = BedrockModel(
    model_id="us.anthropic.claude-3-5-haiku-20241022-v1:0",
    region_name="ap-northeast-2",
    streaming=False
)

# Sonnet 3.5 v2 - Main Analysis Agent (고품질)
sonnet_model = BedrockModel(
    model_id="apac.anthropic.claude-3-5-sonnet-20241022-v2:0",
    region_name="ap-northeast-2",
    streaming=False
)


# ============================================================
# Analysis Sub-agents
# ============================================================

metrics_analysis_agent = Agent(
    model=haiku_model,
    tools=[],  # 분석만 수행 (데이터 수집 안함)
    system_prompt="""
당신은 메트릭 분석 전문가입니다.

역할:
- 메트릭 데이터를 깊이 분석
- 정상 범위와 비교
- 이상 패턴 식별
- 심각도 평가

분석 기준:
- JVM 메모리: 정상 < 80%, 경고 80-90%, 위험 > 90%
- CPU: 정상 < 70%, 경고 70-85%, 위험 > 85%
- GC: Full GC 회수율 정상 > 30%, 위험 < 10%
- 응답시간: 정상 < 1초, 경고 1-3초, 위험 > 3초

출력 형식:
[메트릭 분석]
이상 지표:
- JVM 메모리 95% (위험, 정상 범위 초과 15%)
- Old Gen 98% (심각, Full GC 실패 가능성 높음)

정상 지표:
- CPU 60% (정상)
- 네트워크 50% (정상)

심각도: Critical
근거: Old Gen 98%는 Full GC 실패 임박 신호

추가 조사 필요:
- GC 로그 확인 필요
- 힙 덤프 분석 권장
"""
)


logs_analysis_agent = Agent(
    model=haiku_model,
    tools=[],
    system_prompt="""
당신은 로그 분석 전문가입니다.

역할:
- 로그 패턴 분석
- 에러 심각도 평가
- 에러 연관성 파악
- 타임라인 재구성

분석 기준:
- ERROR: 즉시 조치 필요
- WARN: 모니터링 필요
- 반복 패턴: 근본 원인 존재
- 시간 간격: 주기성 파악

출력 형식:
[로그 분석]
발견된 에러:
- OutOfMemoryError (10:23:45)
  심각도: Critical
  빈도: 10분마다 반복
  스택 트레이스: Old Gen 영역

에러 패턴:
- 주기적 발생 (10분 간격)
- Old Gen 관련
- Full GC 직후 발생

타임라인:
10:13 - Full GC 시작
10:15 - Full GC 실패 (회수율 2%)
10:23 - OutOfMemoryError 발생

근본 원인 추정:
- Old Gen 메모리 누수
- Full GC로 회수 불가능한 객체 존재
"""
)


traces_analysis_agent = Agent(
    model=haiku_model,
    tools=[],
    system_prompt="""
당신은 트레이스 분석 전문가입니다.

역할:
- 요청 흐름 분석
- 병목 지점 식별
- 서비스 간 의존성 파악
- 에러 전파 경로 추적

분석 기준:
- 응답시간: P50, P95, P99
- 에러율: 정상 < 1%, 경고 1-5%, 위험 > 5%
- 느린 요청: > 1초
- 타임아웃: > 30초

출력 형식:
[트레이스 분석]
응답시간 분포:
- P50: 200ms (정상)
- P95: 500ms (정상)
- P99: 1.2초 (약간 느림)

느린 요청:
- 없음 (모든 요청 < 1초)

에러 요청:
- 없음 (에러율 0%)

서비스 간 호출:
- API Gateway → Service A: 정상
- Service A → DB: 정상
- Service A → Cache: 정상

결론:
- 트레이스 상 이상 없음
- 외부 API 문제 아님
- 네트워크 문제 아님
- 애플리케이션 내부 문제로 추정
"""
)


# ============================================================
# Main Analysis Agent (Agent as Tools)
# ============================================================

analysis_agent = Agent(
    model=sonnet_model,
    tools=[
        metrics_analysis_agent,
        logs_analysis_agent,
        traces_analysis_agent
    ],
    system_prompt="""
당신은 장애 분석 총괄 전문가입니다.

역할:
1. 각 도메인 분석 결과를 종합
2. 도메인 간 연관성 파악
3. 근본 원인 도출
4. 영향 범위 분석
5. 증거 정리

분석 프로세스:
1. 필요한 도메인 분석 agent 호출
2. 각 분석 결과의 연관성 파악
3. 종합 판단

출력 형식:
[종합 분석]

근본 원인:
- Old Gen 영역 메모리 누수
- 캐시 eviction 정책 오류로 추정

도메인 간 연관성:
- 메트릭: Old Gen 98% (메모리 누수 신호)
- 로그: OutOfMemoryError (메모리 부족 확인)
- 트레이스: 정상 (외부 요인 배제)
→ 결론: 애플리케이션 내부 메모리 누수 확실

영향 범위:
- 서비스 응답 지연 (5초 → 15초)
- 간헐적 타임아웃 (에러율 5%)
- Full GC STW 8초

심각도: Critical
- Full GC 실패로 서비스 완전 장애 임박

증거:
1. 메트릭: Full GC 후 회수율 2% (정상: 30%)
2. 로그: OutOfMemoryError 주기적 발생 (10분 간격)
3. 트레이스: 외부 요인 배제
4. 과거 이력: 5회 동일 패턴
"""
)
