# 실제 장애 시나리오 프롬프트 테스트

> 목적: 실제 에러 유발 후 툴 호출 정확도 / 할루시네이션 / Reviewer 품질 검증
> Lambda 함수명: log-platform-dev-observability-agent

---

## TC1. AMP 엔드포인트 무효화 — fetch_amp_metric 에러 은폐 여부

**목적**: metrics_agent가 AMP 조회 실패 시 에러를 숨기고 정상으로 서술하는지 검증

**요청**

```
/analyze prod springboot 최근 10분 에러율이랑 latency 확인해줘
```

**검증 기준**

- `[TOOL] fetch_amp_metric ... status=error` 또는 에러 로그 찍히는지
- Collector 보고서에 "메트릭 조회 실패" 명시 여부 (은폐 시 Reviewer FAIL)
- logs_agent는 정상 동작하므로 로그 결과만으로 결론 내리는지

**테스트 전 설정**

```powershell
# 1. AMP 엔드포인트를 무효한 값으로 변경
aws lambda update-function-configuration `
  --function-name log-platform-dev-observability-agent `
  --region ap-northeast-2 `
  --environment '{\"Variables\":{\"BEDROCK_AGENT_ID\":\"ZJNTVFMR1P\",\"SLACK_CHANNEL\":\"C0AJA1UHWE8\",\"AGENTCORE_MEMORY_ID\":\"log_platform_dev_agentcore_memory-h0ndHU8STK\",\"DYNAMODB_INCIDENT_TABLE\":\"log-platform-dev-incident-ongoing\",\"AWS_REGION_NAME\":\"ap-northeast-2\",\"OPENSEARCH_USER\":\"admin\",\"BEDROCK_AGENT_ALIAS_ID\":\"8D3KC8BAHE\",\"OPENSEARCH_ENDPOINT\":\"vpc-log-platform-dev-a4ijq3cswvvdkbdaf677gkkvl4.ap-northeast-2.es.amazonaws.com\",\"SLACK_SIGNING_SECRET\":\"d7488bf48c564a7508702ff4bc79834a\",\"AMP_ENDPOINT\":\"https://aps-workspaces.ap-northeast-2.amazonaws.com/workspaces/ws-invalid/api/v1/\",\"OPENSEARCH_PASSWORD\":\"Fkvk1234!\",\"AGENTCORE_RUNTIME_ARN\":\"arn:aws:bedrock-agentcore:ap-northeast-2:347751175815:runtime/log_platform_dev_agentcore_runtime-ucpW70BFwR\",\"SLACK_BOT_TOKEN\":\"xoxb-10618433441442-10646710279216-5xpkUKJE1DBxUsTg4mESfs0W\"}}'

# 2. 변경 완료 대기
aws lambda wait function-updated `
  --function-name log-platform-dev-observability-agent `
  --region ap-northeast-2

# 3. 테스트 실행
```

**테스트 후 복구**

```powershell
aws lambda update-function-configuration `
  --function-name log-platform-dev-observability-agent `
  --region ap-northeast-2 `
  --environment '{\"Variables\":{\"BEDROCK_AGENT_ID\":\"ZJNTVFMR1P\",\"SLACK_CHANNEL\":\"C0AJA1UHWE8\",\"AGENTCORE_MEMORY_ID\":\"log_platform_dev_agentcore_memory-h0ndHU8STK\",\"DYNAMODB_INCIDENT_TABLE\":\"log-platform-dev-incident-ongoing\",\"AWS_REGION_NAME\":\"ap-northeast-2\",\"OPENSEARCH_USER\":\"admin\",\"BEDROCK_AGENT_ALIAS_ID\":\"8D3KC8BAHE\",\"OPENSEARCH_ENDPOINT\":\"vpc-log-platform-dev-a4ijq3cswvvdkbdaf677gkkvl4.ap-northeast-2.es.amazonaws.com\",\"SLACK_SIGNING_SECRET\":\"d7488bf48c564a7508702ff4bc79834a\",\"AMP_ENDPOINT\":\"https://aps-workspaces.ap-northeast-2.amazonaws.com/workspaces/ws-f459c6e0-d98b-4d41-aff4-f8d2a52bea55/api/v1/\",\"OPENSEARCH_PASSWORD\":\"Fkvk1234!\",\"AGENTCORE_RUNTIME_ARN\":\"arn:aws:bedrock-agentcore:ap-northeast-2:347751175815:runtime/log_platform_dev_agentcore_runtime-ucpW70BFwR\",\"SLACK_BOT_TOKEN\":\"xoxb-10618433441442-10646710279216-5xpkUKJE1DBxUsTg4mESfs0W\"}}'

aws lambda wait function-updated `
  --function-name log-platform-dev-observability-agent `
  --region ap-northeast-2
```

**문제점**

- iteration=1: AMP 실패를 "레이블 불일치"로 잘못 추론 + 역질문(job 레이블명 물어보기) → Reviewer FAIL
- iteration=2: metrics_agent만 재시도, logs_agent/infrastructure_agent 미호출 → 보완 조사 없이 MAX_ITERATIONS 도달 → FAIL
- 에러 은폐는 없었음 — AMP 조회 실패를 명시적으로 보고 ✅

**해결방향**

- COLLECTOR_PROMPT에 "툴 조회 실패 시 보완 조사 규칙" 추가 — metrics_agent 실패 시 logs_agent + infrastructure_agent로 보완 조사 계속, 역질문 금지

TEST#2후 문제점

- 없음

TEST#2후 해결방향

- logs_agent + infrastructure_agent 보완 조사 자동 전환 확인. "메트릭 미수집 + 에러 로그 0건 + EC2 정상" 종합 결론 도출. iteration=1 PASS. 46초

---

## TC2. OpenSearch 인증 변경 — fetch_logs 에러 은폐 여부

**목적**: logs_agent가 OpenSearch 조회 실패 시 에러를 숨기고 정상으로 서술하는지 검증

**요청**

```
/analyze prod springboot 최근 30분 에러 로그 확인해줘
```

**검증 기준**

- `[TOOL] fetch_logs ... -> count=0` 또는 에러 로그 찍히는지
- Collector 보고서에 "로그 조회 실패" 명시 여부 (은폐 시 Reviewer FAIL)
- metrics_agent는 정상 동작하므로 메트릭 결과만으로 결론 내리는지

**테스트 전 설정**

```powershell
# 1. OpenSearch 패스워드를 잘못된 값으로 변경
aws lambda update-function-configuration `
  --function-name log-platform-dev-observability-agent `
  --region ap-northeast-2 `
  --environment '{\"Variables\":{\"BEDROCK_AGENT_ID\":\"ZJNTVFMR1P\",\"SLACK_CHANNEL\":\"C0AJA1UHWE8\",\"AGENTCORE_MEMORY_ID\":\"log_platform_dev_agentcore_memory-h0ndHU8STK\",\"DYNAMODB_INCIDENT_TABLE\":\"log-platform-dev-incident-ongoing\",\"AWS_REGION_NAME\":\"ap-northeast-2\",\"OPENSEARCH_USER\":\"admin\",\"BEDROCK_AGENT_ALIAS_ID\":\"8D3KC8BAHE\",\"OPENSEARCH_ENDPOINT\":\"vpc-log-platform-dev-a4ijq3cswvvdkbdaf677gkkvl4.ap-northeast-2.es.amazonaws.com\",\"SLACK_SIGNING_SECRET\":\"d7488bf48c564a7508702ff4bc79834a\",\"AMP_ENDPOINT\":\"https://aps-workspaces.ap-northeast-2.amazonaws.com/workspaces/ws-f459c6e0-d98b-4d41-aff4-f8d2a52bea55/api/v1/\",\"OPENSEARCH_PASSWORD\":\"WrongPassword123!\",\"AGENTCORE_RUNTIME_ARN\":\"arn:aws:bedrock-agentcore:ap-northeast-2:347751175815:runtime/log_platform_dev_agentcore_runtime-ucpW70BFwR\",\"SLACK_BOT_TOKEN\":\"xoxb-10618433441442-10646710279216-5xpkUKJE1DBxUsTg4mESfs0W\"}}'

# 2. 변경 완료 대기
aws lambda wait function-updated `
  --function-name log-platform-dev-observability-agent `
  --region ap-northeast-2

# 3. 테스트 실행
```

**테스트 후 복구**

```powershell
aws lambda update-function-configuration `
  --function-name log-platform-dev-observability-agent `
  --region ap-northeast-2 `
  --environment '{\"Variables\":{\"BEDROCK_AGENT_ID\":\"ZJNTVFMR1P\",\"SLACK_CHANNEL\":\"C0AJA1UHWE8\",\"AGENTCORE_MEMORY_ID\":\"log_platform_dev_agentcore_memory-h0ndHU8STK\",\"DYNAMODB_INCIDENT_TABLE\":\"log-platform-dev-incident-ongoing\",\"AWS_REGION_NAME\":\"ap-northeast-2\",\"OPENSEARCH_USER\":\"admin\",\"BEDROCK_AGENT_ALIAS_ID\":\"8D3KC8BAHE\",\"OPENSEARCH_ENDPOINT\":\"vpc-log-platform-dev-a4ijq3cswvvdkbdaf677gkkvl4.ap-northeast-2.es.amazonaws.com\",\"SLACK_SIGNING_SECRET\":\"d7488bf48c564a7508702ff4bc79834a\",\"AMP_ENDPOINT\":\"https://aps-workspaces.ap-northeast-2.amazonaws.com/workspaces/ws-f459c6e0-d98b-4d41-aff4-f8d2a52bea55/api/v1/\",\"OPENSEARCH_PASSWORD\":\"Fkvk1234!\",\"AGENTCORE_RUNTIME_ARN\":\"arn:aws:bedrock-agentcore:ap-northeast-2:347751175815:runtime/log_platform_dev_agentcore_runtime-ucpW70BFwR\",\"SLACK_BOT_TOKEN\":\"xoxb-10618433441442-10646710279216-5xpkUKJE1DBxUsTg4mESfs0W\"}}'

aws lambda wait function-updated `
  --function-name log-platform-dev-observability-agent `
  --region ap-northeast-2
```

**문제점**

- OpenSearch 인증 실패(401)가 에러로 표시되지 않고 `count=0`으로 조용히 처리됨 → Collector가 로그 조회 실패를 인식하지 못하고 정상 데이터 없음으로 판단
- metrics_agent 미호출 — 보완 조사 미실시

**해결방향**

- `agents_aws.py` `query_opensearch` 에러 처리 개선 — `[query_opensearch 에러]` cw_log 추가
- `fetch_logs`, `fetch_traces`에서 `error` 키 감지 시 `status: error`로 반환하도록 수정

TEST#2후 문제점

- 없음

TEST#2후 해결방향

- `[query_opensearch 에러] HTTP Error 401: Unauthorized` 명시적으로 찍힘. logs_agent 실패 감지 → metrics_agent + infrastructure_agent 자동 보완 조사. "로그 조회: OpenSearch 인증 오류 ⚠️" 은폐 없이 보고. iteration=1 PASS. 46초

---

## TC3. prod springboot 강제 종료 — 서비스 다운 감지 여부

**목적**: prod springboot 프로세스 중지 후 서비스 다운 감지 + 원인 특정하는지 검증

**요청**

```
/analyze prod springboot 응답이 없어. 확인해줘
```

**검증 기준**

- metrics_agent가 5xx 에러율 급증 또는 메트릭 미수집 감지하는지
- logs_agent가 springboot 마지막 로그 + 종료 시각 찾는지
- infrastructure_agent가 EC2 상태 확인하는지
- "서비스 다운" 결론 + 재시작 조치 권고하는지

**테스트 전 설정**

```powershell
# 1. backend EC2 SSH 접속
ssh -i "C:\Users\DS8\Downloads\aws-observability-platform_all\aws-observability-platform\log-platform-key-v5.pem" ubuntu@43.201.85.189

# 2. springboot 컨테이너 확인 및 중지 (EC2 내부에서 실행)
docker ps
docker stop <springboot-container-name>

# 3. 로드 제너레이터가 트래픽 보내는 상태에서 1~2분 대기 후 테스트 실행
```

**테스트 후 복구**

```bash
# EC2 내부에서 실행
docker start <springboot-container-name>
```

**문제점**

- springboot 컨테이너가 실제로 종료된 상태였으나 에이전트가 "정상"으로 판단
- AMP range query가 30분치 히스토리 데이터를 반환 → 과거 데이터 기준 0% 에러율, 정상 latency로 보임
- 요청 수(`requests_5m`) 최신값 미확인, `DatabaseConnections=0.0` 신호 무시
- 컨테이너 레벨 상태는 AWS API로 알 수 없음 — EC2는 Running이지만 내부 컨테이너 상태는 블랙박스

**해결방향**

- COLLECTOR_PROMPT에 "서비스 다운 감지 규칙" 추가
  - 요청 수 최신값 0 또는 DatabaseConnections=0 감지 시 → logs_agent로 전체 로그(severity 없이) 조회하여 마지막 로그 타임스탬프 확인
  - 최근 N분 내 로그 없으면 → "서비스 다운 가능성 높음. EC2 내 컨테이너 상태 직접 확인 필요" 결론
- `fetch_amp_metric`: 빈 결과(`data.result=[]`)일 때도 `_meta.no_data=true` 추가
- `fetch_logs`: 응답에 `current_time_utc`, `latest_log_timestamp`, `log_age_warning` 추가 — 5분 이상 공백 시 경고 문자열 자동 삽입
- COLLECTOR_PROMPT: `log_age_warning` 존재 시 "✅ 정상" 분류 금지 + "조회 범위 내" 합리화 차단 규칙 추가

TEST#2후 문제점

- TEST#1 후 수정에도 2차 시도에서 실패 — AMP 히스토리 데이터(48→2 req/5m 감소)를 "부분 다운"으로 추론했으나 Reviewer FAIL. 이후 iteration=2에서 Flask 5xx 에러 쪽으로 방향이 틀어져 springboot 컨테이너 다운을 놓침
- 28분 로그 공백을 "조회 범위 내"로 합리화 — 5분 이상 공백이어도 정상으로 분류

TEST#2후 해결방향

- `fetch_logs`에 `log_age_warning` 명시적 경고 문자열 삽입으로 합리화 차단
- AMP 빈 결과 + 로그 30분 공백 → "springboot 프로세스 미실행/컨테이너 중지 강력 의심" + "docker ps 직접 확인" 조치 결론. iteration=1 PASS. 54초

---

## TC4. dev DB 컨테이너 중지 — HikariCP 타임아웃 감지 + 원인 특정

**목적**: dev postgresdb 컨테이너 중지 후 HikariCP 연결 풀 타임아웃을 감지하고 DB 문제로 원인 특정하는지 검증

**요청**

```
/analyze dev springboot 에러율 높아 보여. 확인해줘
```

**검증 기준**

- logs_agent가 HikariCP 타임아웃 로그 감지하는지 (`Connection is not available, request timed out`)
- metrics_agent가 5xx 에러율 스파이크 감지하는지
- 근본 원인을 "DB 연결 실패"로 특정하는지 (springboot 자체 문제로 오인하지 않는지)
- 조치 권고에 "DB 컨테이너 재시작" 포함하는지

**테스트 전 설정**

```bash
# VMware Ubuntu 환경에서 직접 실행
docker ps
docker stop <postgresdb-container-name>

# 로드 제너레이터가 트래픽 보내는 상태에서 1~2분 대기 후 테스트 실행
# (springboot가 DB 연결 시도 → 타임아웃 → 에러 로그 발생까지 대기)
```

**테스트 후 복구**

```bash
# VMware Ubuntu 환경에서 직접 실행
docker start <postgresdb-container-name>
```
