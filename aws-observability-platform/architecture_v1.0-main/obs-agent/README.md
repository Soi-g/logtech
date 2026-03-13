# obs-agent

Observability AI Agent용 Lambda 코드 (Telemetry / Runbook / Remediation).
AMP, OpenSearch, S3 Runbooks를 조회하고, 나중에 Bedrock Agent Action Group에 연결한다.

## Lambda 패키지 빌드 (Windows / zip 없이)

```powershell
cd obs-agent

# 1) 의존성 설치 (최초 1회 또는 requirements.txt 변경 시)
pip install -r requirements.txt -t package

# 2) dist/telemetry.zip 생성
python build.py
```

이후 Terraform에서 `modules/ai_agent` 가 `obs-agent/dist/telemetry.zip` 을 참조하므로,  
`aws-observability-platform` 디렉터리에서 `terraform apply -target=module.ai_agent` 로 배포하면 된다.

## 로컬 테스트

Lambda 콘솔에서 테스트 시 `events/telemetry-test.json` 내용을 이벤트로 넣어 실행하면 된다.
