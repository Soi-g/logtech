# /analyze 슬래시 커맨드 테스트 결과

> 테스트 일자: 2026-03-26
> 목적: 툴 호출 정확도 / 할루시네이션 / Reviewer 품질 검증

---

## dev/prod 동시 비교

`/analyze dev랑 prod springboot 최근 5분 에러율이랑 latency 비교해줘`

**문제점**

- metrics_agent 2개가 동시 호출 시도됐으나 prod 완료 후 dev가 순차 처리됨 (병렬 호출 미실현)

**해결방향**

- 수정 불필요. 결과 정확도·환경 분리에는 문제 없음. 병렬 호출 일관성은 패턴 축적 후 판단

---

## EC2 CPU CloudWatch 조회

`/analyze prod EC2 CPU 사용률 확인해줘`

**문제점**

- iteration=1에서 툴을 호출하지 않고 "어느 EC2인지, 시간 범위는?" 역질문 텍스트만 반환 → Reviewer FAIL
- iteration=2에서 조회는 했으나 Reviewer FAIL (MAX_ITERATIONS 도달)

**해결방향**

- COLLECTOR_PROMPT에 "대상 불명확 시 역질문 금지 — 알려진 리소스 전체를 합리적 기본값으로 즉시 조회" 규칙 추가

---

TEST#2후 문제점

- 없음

TEST#2후 해결방향

- iteration=1에서 frontend/backend EC2 전체 즉시 조회 후 PASS. 소요 시간 51초 → 30초 단축

---

## traces 생략 요청

`/analyze dev springboot 최근 5분 에러율만 확인해줘. 트레이스는 필요 없어`

**문제점**

- 없음

**해결방향**

- 불필요. traces 미호출·로그만 조회·env=dev 자동 감지 모두 처음부터 정상 동작

---

## 정상 상태 빈 RCA

`/analyze dev springboot 최근 10분 상태 요약해줘`

**문제점**

- 없음

**해결방향**

- 불필요. 정상 상태에서 likely_root_causes/actions 모두 빈 배열, 원인 지어내지 않음 확인

---

## 모호한 요청 드릴다운

`/analyze prod springboot 에러율이 높아 보여. 원인 찾아줘`

**문제점**

- iteration=1에서 latency 스파이크를 발견했으나 원인을 "메모리 누수/GC/동시 요청/DB 타임아웃 등 가능"으로 증거 없이 가설만 나열 → Reviewer FAIL

**해결방향**

- 별도 프롬프트 수정 불필요. Reviewer가 증거 없는 가설 나열을 올바르게 FAIL 처리 → iteration=2에서 JVM 메모리 30.3MB→27.6MB 급락 데이터로 GC 원인 확정, PASS. iteration 구조가 의도대로 동작한 것으로 판단

---

## 존재하지 않는 서비스

`/analyze dev payment-service 최근 10분 에러율이랑 로그 확인해줘`

**문제점**

- iteration=1에서 툴 미호출 후 "payment-service가 토폴로지에 없다"는 텍스트만 반환 → Reviewer FAIL. iteration=2에서도 툴 미호출, 결론 텍스트만 → Reviewer PASS했으나 비효율적

**해결방향**

- COLLECTOR_PROMPT에 역질문 금지 규칙 추가 (EC2 CPU 조회 테스트와 동일 수정)

---

TEST#2후 문제점

- 역질문은 없어졌으나 Reviewer FAIL×2 (MAX_ITERATIONS 도달). iteration=1 결론에 "다음 중 하나로 진행하세요" 안내 포함 → Reviewer가 미완성 보고서로 판단. iteration=2에서도 "조사 불가 상태 / 필요 정보 목록" → FAIL

TEST#2후 해결방향

- REVIEWER_PROMPT에 "툴 조회 후 데이터 전혀 없는 경우 → 유효한 완결 결론으로 PASS 처리" 규칙 추가. 단, "서비스 미등록"으로 단정하지 않고 복수 가능성(서비스명 불일치/미배포/OTel 수집 실패) 나열하는 것도 허용

---

TEST#3후 문제점

- 없음

TEST#3후 해결방향

- iteration=1에서 즉시 조회 후 "AMP 미등록/미수집" + 가능한 원인 3가지 나열, PASS. 62초 → 28초 단축

---

## 시간 파싱 + 단순 에러율 조회

`/analyze prod springboot 에러율 최근 1시간 알려줘`

**문제점**

- "에러율"이라고 요청했으나 5xx만 조회하고 4xx는 미조회. 사용자 입장에서 에러율은 4xx+5xx를 모두 포함하는 게 일반적
- logs_agent 미호출은 단순 조회 요청이라 적절

**해결방향**

- METRICS_AGENT_PROMPT에 "에러율 단독 요청 시 4xx+5xx 둘 다 조회" 규칙 추가
- COLLECTOR_PROMPT Http5xxErrorRate 초기 호출 기준도 `4xx+5xx 에러율`로 수정
- 단, "1시간" → last_minutes=60 시간 파싱은 처음부터 정상 동작 확인 ✅

---

TEST#2후 문제점

- 없음

TEST#2후 해결방향

- 4xx+5xx 동시 조회 확인. PASS (iteration=1), 35초

---

## 시간 파싱 — 한국어 절대 시각

`/analyze dev springboot 오늘 오전 9시부터 에러 있었어?`

**문제점**

- "오늘 오전 9시부터" → metrics_agent가 `last_minutes=0` (instant query, 현재 순간값)으로 처리 — 시간 범위 조회 실패
- logs_agent는 `last_minutes=720` (12시간)으로 처리해서 실제 에러를 발견했으나 요청 시간보다 훨씬 넓은 범위
- iteration=1에서 에러율 0%를 "현재 요청이 처리 중이지 않음을 시사"로 잘못 해석 → Reviewer FAIL
- iteration=2에서 과거 에러(7시간 전 HikariCP 연결 풀 고갈) + 현재 정상 복구 상태 올바르게 판단 → PASS

**해결방향**

- COLLECTOR_PROMPT 시간 파싱 섹션에 "오전/오후 N시" 절대 시각 처리 추가 — 현재 시각 기준 분 차이 계산 명시 (예: 현재 17:30 KST, "오전 9시" → last_minutes=510)
- 단, 에러 데이터는 정상 발견(HikariCP 39건) 및 최종 결론(과거 장애 + 현재 복구)은 올바름

---

TEST#2후 문제점

- 없음

TEST#2후 해결방향

- last_minutes=480 (현재 18:04 KST 기준 오전 9시까지 8시간) 정상 변환 확인. iteration=1에서 PASS. 5xx 에러율 100% + latency 10초 스파이크 + HikariCP 로그 39건 교차 검증 후 올바른 결론 도출

---

## DB 장애 + CloudTrail

`/analyze prod 고객 환경에서 30분 전부터 DB 타임아웃이 간헐적으로 발생하고 있어. RDS 상태, 에러 로그, 트레이스, CloudTrail 변경 이력까지 전부 확인해줘`

**문제점**

- 사용자가 "트레이스 전부 확인해줘"라고 명시했음에도 fetch_traces 툴 미호출 — logs_agent가 fetch_logs로 대체
- iteration=1에서 CloudTrail 보안그룹 Revoke 이벤트 발견 즉시 "이것이 원인"으로 단정 → latency 스파이크 시각과의 타임라인 검증 없이 결론 → Reviewer FAIL

**해결방향**

- LOGS_AGENT_PROMPT에 fetch_traces 사용 조건 명시 — query에 "트레이스/traces/span" 포함 시 fetch_traces 사용, 그 외엔 fetch_logs 사용
- COLLECTOR_PROMPT에 CloudTrail 인과관계 검증 4단계 규칙 추가 (증상 시각 확인 → 이벤트 시각이 증상 이전인지 확인 → 실제 영향 데이터 검증 → 3가지 일치 시에만 인과관계 결론)
- REVIEWER_PROMPT에 TIMESTAMP CAUSALITY 기준 추가 — CloudTrail 이벤트 시각이 알람 발생 이전인지 검증 필수

---

TEST#2후 문제점

- fetch_traces는 해결됨. iteration=1에서 여전히 "간헐적 타임아웃 패턴 일치 ✅" 표현이 남아 Reviewer FAIL

TEST#2후 해결방향

- iteration=2에서 스스로 모순 발견 (latency 스파이크 시각이 보안그룹 변경보다 앞서 발생 → 인과관계 부정) 후 "인과관계 결정 불가" 결론으로 수정, PASS. iteration=1 패턴 잔존은 추가 개선 여지 있으나 iteration=2에서 자동 수정되므로 현재는 허용

---

## env 미지정 시 dev/prod 선택

`/analyze springboot 에러율 확인해줘`

**문제점**

- env 미지정 시 prod로 기본값 처리 — dev 환경은 조회하지 않음. dev에 실제 에러가 있어도 확인 불가

**해결방향**

- bedrock_agent_runtime_handler.py env 감지 로직 수정: dev/prod 둘 다 없으면 `environment=both`로 설정
- graph_agent_with_memory.py `_make_collector`: `both`일 때 infrastructure_agent 포함
- COLLECTOR_PROMPT에 `deployment_environment=both` 시 dev/prod 각각 조회 규칙 추가

---

TEST#2후 문제점

- 없음

TEST#2후 해결방향

- `[collector] env=both` 확인. dev/prod 각각 metrics_agent 호출 후 환경별 구분 결과 테이블 출력. iteration=1 PASS. 31초

---

## env=both 수치 혼재 없이 구분되는지

`/analyze prod랑 dev 둘 다 상태 요약해줘`

**문제점**

- 없음

**해결방향**

- 불필요. dev/prod 환경별로 완전히 구분된 결과 출력 확인. infrastructure_agent는 prod만 조회 (dev는 로컬 Docker — AWS API 조회 불가, 의도된 동작). iteration=1 PASS. 62초

---

## 복잡한 연쇄 장애 시나리오 — 계층별 드릴다운 순서

`/analyze prod flask가 502 반환하고 있어. springboot쪽 문제인지 RDS 문제인지 확인해줘`

**문제점**

- 없음

**해결방향**

- 불필요. Flask → Springboot → RDS 계층별 드릴다운을 첫 턴에 병렬 호출 확인. 전 계층 정상으로 데이터 없음 → 일시적 에러 / 시간대 차이 가능성 나열 (단정 없음). iteration=1 PASS. 35초

---

## 경계 케이스 — 수치 해석 + actions 과도 채우기

`/analyze prod springboot 에러율 1.5%인데 심각한거야?`

**문제점**

- iteration=1에서 데이터 조회 후 0% 확인했으나 "추천 다음 단계" 역질문 안내 목록 포함 → Reviewer FAIL

**해결방향**

- 수정 불필요. Reviewer가 역질문 포함 보고서를 올바르게 FAIL 처리 → iteration=2에서 조회 범위 2시간으로 확대 후 역질문 없이 결론 도출, PASS. iteration 구조가 의도대로 동작한 것으로 판단
- iteration=2에서 사용자 미요청에도 fetch_traces(status_filter='Error') 자동 호출 — 에러 원인 추적에 직접적인 증거이므로 허용

---

## 넓은 범위 요청 처리

`/analyze prod 전체 서비스 다 확인해줘`

**문제점**

- 없음

**해결방향**

- 불필요. flask/thymeleaf/springboot + RDS + EC2 + CloudWatch 알람 전 계층을 역질문 없이 첫 턴에 병렬 호출 확인. iteration=1 PASS. 47초

---

## 실제 장애 — Flask 5xx 알람 분석 버튼 (AgentCore Runtime 경로)

> 슬래시 커맨드가 아닌 알람 버튼 클릭 → AgentCore Runtime 컨테이너 경로로 실행된 실제 장애 분석

**발생 상황**

- Flask 5xx + Thymeleaf 5xx + Springboot ServiceDown 알람 3개 동시 발생
- Flask 알람의 "분석" 버튼 클릭 → AgentCore Runtime에서 자동 분석 시작

**검증 결과**

- `[query_opensearch 에러] [Errno -2] Name or service not known` 명시적으로 찍힘 — OpenSearch 조회 실패 은폐 없음 ✅
- logs_agent 실패 감지 → metrics_agent + infrastructure_agent 자동 보완 조사 전환 ✅
- CloudTrail에서 `11:24:12 UTC RevokeSecurityGroupIngress` 이벤트 발견
- 보안그룹 변경(11:24) → Flask 5xx 알람(11:33) 타임라인 인과관계 검증 포함
- \"보안그룹 Ingress 규칙 제거로 Flask↔Springboot 통신 차단 → 5xx 에러\" 결론
- iteration=1 PASS

**확인된 수정 사항 동작 검증**

- `_log()` 헬퍼: AgentCore 경로에서도 `/agentcore/runtime/analysis` 로그 그룹에 [TOOL], [SUB-AGENT] 로그 수집 ✅
- `fetch_logs` 에러 처리: OpenSearch 실패 시 `status: error` 반환 → Collector가 인식 후 보완 조사 ✅
- COLLECTOR_PROMPT 보완 조사 규칙: 툴 조회 실패 시 다른 에이전트로 자동 전환 ✅
- REVIEWER_PROMPT CloudTrail 인과관계 검증: 타임라인 검증 포함 결론만 PASS ✅
