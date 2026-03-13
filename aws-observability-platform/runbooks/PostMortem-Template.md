# Post-Mortem: [장애명]

> 작성일: YYYY-MM-DD  
> 작성자: [이름]  
> 심각도: Critical / High / Medium

## 📋 장애 개요

- **발생 시각**: YYYY-MM-DD HH:MM UTC
- **종료 시각**: YYYY-MM-DD HH:MM UTC
- **지속 시간**: X시간 Y분
- **영향 범위**: [서비스명, 사용자 수, 지역 등]
- **비즈니스 영향**: [매출 손실, 사용자 불만 등]

## 🔍 타임라인

| 시각 | 이벤트 | 담당자 |
|------|--------|--------|
| 14:30 | HighJvmMemory 알람 발생 | 시스템 |
| 14:32 | 온콜 엔지니어 확인 시작 | 홍길동 |
| 14:35 | 메모리 덤프 수집 | 홍길동 |
| 14:40 | 원인 파악 (메모리 누수) | 홍길동 |
| 14:45 | 긴급 재시작 결정 | 팀장 |
| 14:50 | 서비스 재시작 완료 | 홍길동 |
| 14:55 | 정상화 확인 | 홍길동 |

## 🎯 근본 원인 (Root Cause)

### 직접 원인
- UserCache 클래스에서 메모리 누수 발생
- 캐시 만료 정책이 설정되지 않아 무한 증가

### 기술적 상세
```java
// 문제 코드
private static Map<String, User> userCache = new HashMap<>();

public User getUser(String id) {
    if (!userCache.containsKey(id)) {
        User user = database.findUser(id);
        userCache.put(id, user);  // 만료 없이 계속 추가
    }
    return userCache.get(id);
}
```

### 왜 발생했는가?
1. 코드 리뷰에서 캐시 만료 정책 누락
2. 성능 테스트 시 장시간 테스트 미실시
3. 메모리 모니터링 알람 임계값이 너무 높음 (85%)

## 📊 영향 분석

### 메트릭 데이터
- JVM 메모리 사용률: 85% → 95% (10분간)
- API 응답시간: P95 500ms → 5000ms
- 에러율: 0.1% → 15%
- 영향받은 요청 수: 약 50,000건

### 사용자 영향
- 로그인 실패: 약 5,000명
- 페이지 로딩 지연: 약 45,000명
- 고객 문의: 23건

## ✅ 즉시 조치 (Immediate Actions)

1. **14:45** - 서비스 긴급 재시작
2. **14:50** - 메모리 사용률 정상화 확인
3. **15:00** - 사용자 공지 (상태 페이지)
4. **15:30** - 핫픽스 배포 (캐시 크기 제한)

## 🔧 근본 해결 (Permanent Fix)

### 코드 수정
```java
// 수정 후 코드
private static LoadingCache<String, User> userCache = CacheBuilder.newBuilder()
    .maximumSize(10_000)           // 최대 10,000개
    .expireAfterWrite(1, TimeUnit.HOURS)  // 1시간 후 만료
    .build(new CacheLoader<String, User>() {
        public User load(String id) {
            return database.findUser(id);
        }
    });
```

### 배포 일정
- **핫픽스**: 2026-03-01 15:30 (완료)
- **정식 릴리스**: 2026-03-05 v2.3.1

## 🛡️ 재발 방지 (Prevention)

### 단기 (1주일 내)
- [x] 메모리 알람 임계값 85% → 70% 하향
- [x] 모든 캐시 구현체 리뷰 및 만료 정책 추가
- [x] 메모리 프로파일링 도구 설정

### 중기 (1개월 내)
- [ ] 성능 테스트에 장시간 부하 테스트 추가 (24시간)
- [ ] 메모리 누수 자동 탐지 도구 도입
- [ ] 캐시 사용 가이드라인 문서화

### 장기 (3개월 내)
- [ ] 코드 리뷰 체크리스트에 리소스 관리 항목 추가
- [ ] 자동화된 메모리 프로파일링 CI/CD 통합
- [ ] 캐시 모니터링 대시보드 구축

## 📚 학습 내용 (Lessons Learned)

### 잘한 점
- 알람 발생 후 2분 내 대응 시작
- 메모리 덤프를 빠르게 수집하여 원인 파악
- 명확한 커뮤니케이션 (Slack, 상태 페이지)

### 개선할 점
- 코드 리뷰에서 캐시 만료 정책 체크 누락
- 성능 테스트가 짧아서 메모리 누수 미발견
- 알람 임계값이 너무 높아 늦게 감지

### 액션 아이템
| 항목 | 담당자 | 기한 | 상태 |
|------|--------|------|------|
| 캐시 가이드라인 작성 | 홍길동 | 2026-03-08 | 진행중 |
| 성능 테스트 개선 | 김철수 | 2026-03-15 | 예정 |
| 알람 임계값 조정 | 이영희 | 2026-03-02 | 완료 |

## 🔗 관련 자료

- [Slack 스레드](https://workspace.slack.com/archives/...)
- [메모리 덤프 분석 결과](s3://...)
- [관련 Jira 티켓](https://jira.company.com/...)
- [배포 PR](https://github.com/company/repo/pull/123)

## 📝 참고 런북

- [HighJvmMemory.md](./HighJvmMemory.md) - JVM 메모리 대응 절차
- [EmergencyRollback.md](./EmergencyRollback.md) - 긴급 롤백 절차

---

**검토자**: [팀장 이름]  
**승인일**: YYYY-MM-DD  
**다음 리뷰**: 3개월 후 (YYYY-MM-DD)
