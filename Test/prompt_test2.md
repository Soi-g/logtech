# 고급 장애 시나리오 프롬프트 테스트

> 목적: TC1~TC4보다 애매한 장애 상황에서 에이전트의 원인 특정 정확도 / 오판 여부 검증
> Lambda 함수명: log-platform-dev-observability-agent

---

## TC5. Slow DB (연결 홀딩) — DB 다운 vs DB 지연 구분 여부

**목적**: DB가 살아있지만 커넥션을 점유해 HikariCP 풀이 소진되는 상황에서, TC4(DB 완전 다운)와 구분하여 "DB 응답 지연"으로 특정하는지 검증

**요청**

```
/analyze dev springboot 응답이 엄청 느려졌어. 확인해줘
```

**검증 기준**

- metrics_agent가 `app_db_connection_wait_p95_5m` 급등 감지하는지
- metrics_agent가 5xx 에러율 상승 + latency P95 급등 동시 감지하는지
- 로그에서 `HikariPool - Connection is not available, request timed out` 찾는지
- "DB 다운"이 아닌 **"DB 응답 지연 / 커넥션 홀딩"** 으로 원인 특정하는지 (DB 재시작 권고가 아닌 slow query 조사 권고)

**테스트 전 설정**

```bash
# VMware Ubuntu 환경에서 실행
# PostgreSQL에 슬리프 커넥션 다수 생성 (HikariCP 기본 풀 크기: 10)
# ⚠️ docker exec -d 사용 금지 — 백그라운드 셸 job으로 실행해야 커넥션이 유지됨
for i in {1..12}; do
  docker exec lfs148-code_v2-postgresdb-1 \
    psql -U matthias -d mydb -c "SELECT pg_sleep(300);" &
done

# 커넥션 점유 확인 (12~13개면 성공)
docker exec lfs148-code_v2-postgresdb-1 \
  psql -U matthias -d mydb -c "SELECT count(*) FROM pg_stat_activity WHERE state = 'active';"

# HikariCP 타임아웃까지 30~60초 대기 후 테스트 실행
# springboot 에러 로그 발생 확인: docker logs lfs148-code_v2-todobackend-springboot-1 --tail 5
```

**테스트 후 복구**

```bash
# 슬리프 쿼리 강제 종료
docker exec lfs148-code_v2-postgresdb-1 \
  psql -U matthias -d mydb -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE query LIKE '%pg_sleep%';"

# springboot 정상화 확인 (HikariCP 자동 재연결)
docker logs lfs148-code_v2-todobackend-springboot-1 --tail 20
```

**오판 위험**

- TC4(DB 완전 다운)와 HikariCP 에러 로그가 동일하게 보임 → "DB 컨테이너 중지" 로 오판 가능
- DB는 살아있으므로 `_meta.no_data=true` 없이 AMP 메트릭도 정상 수집됨 → 에러율만으로는 원인 불분명
- `app_db_connection_wait_p95_5m` 과 `app_db_connection_use_p95_5m` 두 지표 비교가 핵심

**문제점**

- `docker exec -d`(detach)로 pg_sleep 생성 시 120초 후 커넥션 자동 만료 → 테스트 실행 전에 이미 복구됨 (타이밍 미스)
- iteration=1 FAIL: `log_age_warning`(15분 로그 공백) 존재 시 latency P95 = 9~16ms (정상) 신호를 "캐시된 값일 것"으로 합리화하고 "서비스 다운 강력 의심"으로 단정 → 메트릭 신선도(17초)와 교차 검증 안 함
- 결과적으로 "Slow DB 진행 중" 시나리오가 아닌 "과거 장애 + 자동 복구" 시나리오로 변질됨

**해결방향**

- 테스트 설정: `docker exec -d` → bash background job (`docker exec ... &`) + pg_sleep(300)으로 변경하여 커넥션 점유 시간 확보
- 에이전트 개선 필요: `log_age_warning` 있어도 메트릭 신선도가 17초 이내이고 latency 정상이면 "현재 정상, 과거 장애" 로 분류해야 함 — `log_age_warning`만으로 현재 상태를 "다운"으로 단정하는 로직 완화 필요
- iteration=2 PASS (과거 장애 + 자동 복구 패턴 정확히 분류)

---

## TC6. CPU 스파이크 → P95 레이턴시 급등 — 에러율 0%인 성능 장애 감지

**목적**: 5xx 에러가 없는 상태에서 CPU 100% 부하로 응답 latency만 급등할 때, 에이전트가 "정상"으로 오판하지 않고 성능 장애로 분류하는지 검증

**요청**

```
/analyze dev springboot 요청이 매우 느려졌다는 신고가 있어. 확인해줘
```

**검증 기준**

- metrics_agent가 `app_http_server_latency_p95_5m` 급등 감지하는지
- metrics_agent가 `app_container_cpu_utilization_avg_5m` 또는 `app_container_cpu_utilization_max_5m` 이상 감지하는지
- **5xx 에러율 0%임에도** 성능 장애로 분류하는지 (에러 없음 = 정상으로 오판하지 않는지)
- 근본 원인을 "CPU 과부하로 인한 응답 지연"으로 특정하는지
- 조치 권고에 "CPU 부하 원인 프로세스 확인" 또는 "컨테이너 리소스 제한 확인" 포함하는지

**테스트 전 설정**

```bash
# VMware Ubuntu 환경에서 실행

# stress 설치 (없는 경우)
docker exec lfs148-code_v2-todobackend-springboot-1 apt-get install -y stress 2>/dev/null || true

# CPU 100% 부하 (5분간)
docker exec -d lfs148-code_v2-todobackend-springboot-1 stress --cpu 4 --timeout 300

# CPU 점유 확인
docker stats lfs148-code_v2-todobackend-springboot-1 --no-stream

# 로드 제너레이터가 트래픽 보내는 상태에서 1~2분 대기 후 테스트 실행
# (메트릭 집계 interval=30s 감안 — 최소 2~3 포인트 쌓인 후 실행)
```

**테스트 후 복구**

```bash
# stress 프로세스 종료 (timeout 300 자동 종료 대기 또는 강제 종료)
docker exec lfs148-code_v2-todobackend-springboot-1 pkill -f stress || true

# CPU 정상화 확인
docker stats lfs148-code_v2-todobackend-springboot-1 --no-stream
```

**오판 위험**

- 5xx 에러율 = 0% → Collector가 "에러 없음, 정상" 으로 결론 내릴 가능성 높음
- latency P95 값만 상승 → "일시적 트래픽 증가"로 합리화할 수 있음
- CPU 메트릭은 `app_container_cpu_utilization_avg_5m` 조회해야 보임 — metrics_agent가 이 메트릭을 자발적으로 조회하는지가 핵심

**문제점**

- (미실시)

**해결방향**

- (미실시)

---

## TC7. 특정 엔드포인트만 4xx 폭증 — 서비스 이상 vs 클라이언트 버그 구분

**목적**: 존재하지 않는 엔드포인트에 반복 요청을 보내 4xx 에러율을 급증시켰을 때, 에이전트가 "서비스 장애"로 오판하지 않고 "클라이언트 측 잘못된 요청 / 특정 경로 문제"로 구분하는지 검증

**요청**

```
/analyze dev springboot 4xx 에러가 갑자기 늘었어. 확인해줘
```

**검증 기준**

- metrics_agent가 `app_http_server_4xx_errors_5m` 또는 `job:http_4xx_error_ratio:rate5m` 이상 감지하는지
- **어떤 http_route에서 4xx가 발생했는지** 경로 레벨로 특정하는지
- logs_agent가 해당 경로의 404 로그 찾는지
- 5xx 에러율 및 전체 서비스 정상 여부를 함께 확인하는지
- 결론을 "서비스 장애"가 아닌 **"특정 경로 클라이언트 에러 / False Positive 가능성"** 으로 분류하는지

**테스트 전 설정**

```bash
# EC2 또는 로컬에서 실행
# EC2 Public IP 확인: terraform output otel_collector_public_ip

EC2_IP="43.201.85.189"

# 존재하지 않는 엔드포인트 대량 호출 (300초간 0.3초 간격 = 약 1000회)
# 실제 서비스 엔드포인트(/todos/)는 건드리지 않음
for i in $(seq 1 1000); do
  curl -s "http://${EC2_IP}:8080/api/nonexistent/endpoint" > /dev/null
  curl -s "http://${EC2_IP}:8080/todos/99999999" > /dev/null
  sleep 0.3
done &

# 호출 시작 후 1분 대기 → 메트릭 집계 후 테스트 실행
```

**테스트 후 복구**

```bash
# 백그라운드 curl 루프 종료
kill %1 2>/dev/null || pkill -f "curl.*nonexistent" || true

# 에러율 정상화 확인 (약 5분 후 rate 윈도우 소멸)
```

**오판 위험**

- `Http4xxErrorRate` alert 발화 → 에이전트가 "서비스 장애 알람 발생"으로 심각도 과대 평가 가능
- `Unexpected4xxDetected` alert도 동시 발화 가능 → 이중 알람으로 혼란
- 5xx 에러율 0% + 전체 요청 count 정상인 신호를 함께 봐야 "서비스 자체 이상 없음" 판단 가능
- `http_route` 라벨로 문제 경로를 pinpoint 하는 것이 핵심 — 전체 job 레벨 집계만 보면 놓침

**문제점**

- (미실시)

**해결방향**

- (미실시)
