# 배포 후 수동 작업 가이드

## 1. AOSS 인덱스 생성

`terraform apply` 완료 후 AOSS 인덱스를 생성해야 합니다.

### PowerShell 스크립트 사용 (권장)

```powershell
# Terraform output에서 엔드포인트 확인
terraform output

# 스크립트 실행
.\scripts\setup_indexes.ps1 `
    -IncidentMemoryEndpoint "https://YOUR_INCIDENT_MEMORY_ENDPOINT.ap-northeast-2.aoss.amazonaws.com" `
    -RunbooksEndpoint "https://YOUR_RUNBOOKS_ENDPOINT.ap-northeast-2.aoss.amazonaws.com"
```

### 수동 실행

#### Incident Memory 인덱스

```powershell
# 1. payload 파일 생성
@"
{
  "endpoint": "https://YOUR_INCIDENT_MEMORY_ENDPOINT.ap-northeast-2.aoss.amazonaws.com",
  "region": "ap-northeast-2",
  "index_name": "incident-memory-index"
}
"@ | Out-File -FilePath incident_memory_payload.json -Encoding utf8

# 2. 60초 대기 (AOSS 정책 전파)
Start-Sleep -Seconds 60

# 3. Lambda 호출
aws lambda invoke `
    --function-name log-platform-dev-incident-memory-index-creator `
    --region ap-northeast-2 `
    --payload file://incident_memory_payload.json `
    incident_memory_response.json

# 4. 결과 확인
Get-Content incident_memory_response.json
```

#### Knowledge Base 인덱스

```powershell
# 1. payload 파일 생성
@"
{
  "endpoint": "https://YOUR_RUNBOOKS_ENDPOINT.ap-northeast-2.aoss.amazonaws.com",
  "region": "ap-northeast-2",
  "index_name": "bedrock-knowledge-base-default-index"
}
"@ | Out-File -FilePath kb_index_payload.json -Encoding utf8

# 2. 30초 대기
Start-Sleep -Seconds 30

# 3. Lambda 호출
aws lambda invoke `
    --function-name log-platform-dev-aoss-index-creator `
    --region ap-northeast-2 `
    --payload file://kb_index_payload.json `
    kb_index_response.json

# 4. 결과 확인
Get-Content kb_index_response.json
```

---

## 2. OpenSearch 역할 매핑 (선택)

OpenSearch Ingestion Pipeline이 OpenSearch에 데이터를 쓸 수 있도록 역할 매핑이 필요합니다.

### EC2에 SSH 접속 후 실행

```bash
# EC2 Public IP 확인
terraform output otel_collector_public_ip

# SSH 접속
ssh -i your-key.pem ubuntu@EC2_PUBLIC_IP

# OpenSearch 역할 매핑
curl -sk -u 'admin:YOUR_PASSWORD' -X PUT \
  'https://YOUR_OPENSEARCH_ENDPOINT/_plugins/_security/api/rolesmapping/all_access' \
  -H 'Content-Type: application/json' \
  -d '{
    "backend_roles": ["arn:aws:iam::ACCOUNT_ID:role/log-platform-dev-osis-pipeline-role"],
    "users": ["admin"]
  }'

# ISM 정책 생성 (30일 후 자동 삭제)
curl -sk -u 'admin:YOUR_PASSWORD' -X PUT \
  'https://YOUR_OPENSEARCH_ENDPOINT/_plugins/_ism/policies/delete-after-30-days' \
  -H 'Content-Type: application/json' \
  -d '{
    "policy": {
      "description": "Delete indices after 30 days",
      "default_state": "hot",
      "states": [
        {
          "name": "hot",
          "actions": [],
          "transitions": [{
            "state_name": "delete",
            "conditions": {"min_index_age": "30d"}
          }]
        },
        {
          "name": "delete",
          "actions": [{"delete": {}}],
          "transitions": []
        }
      ]
    }
  }'
```

---

## 3. Knowledge Base 동기화

런북 파일을 S3에 업로드하면 자동으로 Knowledge Base에 동기화됩니다.

```powershell
# 런북 업로드
aws s3 cp runbooks/ s3://log-platform-dev-runbooks-ACCOUNT_ID/runbooks/ --recursive

# 동기화 확인 (Lambda 로그)
aws logs tail /aws/lambda/log-platform-dev-kb-sync --follow
```

---

## 4. 테스트

### SNS 테스트 메시지 발송

```powershell
aws sns publish `
    --topic-arn "arn:aws:sns:ap-northeast-2:ACCOUNT_ID:log-platform-dev-alerts" `
    --message '{
      "AlarmName": "HighJvmMemory",
      "AlarmDescription": "JVM 메모리 85% 초과",
      "NewStateValue": "ALARM",
      "StateChangeTime": "2024-01-01T00:00:00.000Z"
    }'
```

### Lambda 로그 확인

```powershell
aws logs tail /aws/lambda/log-platform-dev-observability-agent --follow
```

---

## 트러블슈팅

### AOSS 인덱스 생성 실패 (403 Forbidden)

AOSS 정책 전파에 최대 5분 소요됩니다. 60초 대기 후 재시도하세요.

```powershell
Start-Sleep -Seconds 60
# Lambda 재호출
```

### Knowledge Base 생성 실패 (404 no such index)

KB 인덱스가 생성되지 않았습니다. 수동으로 생성 후 `terraform apply` 재실행하세요.

```powershell
# KB 인덱스 생성
.\scripts\setup_indexes.ps1 -IncidentMemoryEndpoint "..." -RunbooksEndpoint "..."

# Terraform 재실행
terraform apply
```

### Lambda VPC 타임아웃

Lambda가 VPC 내부에서 실행되므로 NAT Gateway를 통해 인터넷에 접근합니다.

- NAT Gateway 상태 확인
- Security Group 아웃바운드 규칙 확인
- VPC 엔드포인트 추가 고려 (Bedrock, AOSS)
