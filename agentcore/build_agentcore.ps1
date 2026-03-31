# AgentCore Runtime 빌드 및 배포 스크립트 (Windows PowerShell)
# terraform apply 후 실행 (프로젝트 루트에서): .\agentcore\build_agentcore.ps1

$ErrorActionPreference = "Stop"
$Region      = "ap-northeast-2"
$AccountId   = (aws sts get-caller-identity --query Account --output text).Trim()
$EcrUrl      = (terraform output -raw ecr_repository_url).Trim()
$RoleArn     = (terraform output -raw agentcore_runtime_role_arn).Trim()
$RuntimeName = "log_platform_dev_agentcore_runtime"
$ImageUri    = "${EcrUrl}:latest"

Write-Host "ECR URL  : $EcrUrl"
Write-Host "Image URI: $ImageUri"

# ── [1/4] ECR 로그인 ──────────────────────────────────────────
Write-Host "`n[1/4] ECR Login..."
$EcrPassword = aws ecr get-login-password --region $Region
docker login --username AWS --password $EcrPassword "$AccountId.dkr.ecr.$Region.amazonaws.com"

# ── [2/4] Docker 빌드 ─────────────────────────────────────────
Write-Host "`n[2/4] Docker Image Build..."
docker build --platform linux/arm64 -t "${RuntimeName}:latest" -f agentcore/Dockerfile .

# ── [3/4] ECR push ────────────────────────────────────────────
Write-Host "`n[3/4] ECR push..."
docker tag "${RuntimeName}:latest" $ImageUri
docker push $ImageUri

# ── [4/4] AgentCore Runtime 생성 또는 업데이트 ────────────────
Write-Host "`n[4/4] AgentCore Runtime Create or Update..."

# terraform.tfvars에서 값 읽기
$TfVars = Get-Content terraform.tfvars -Raw
function Get-TfVar($name) {
    if ($TfVars -match "$name\s*=\s*`"([^`"]+)`"") { return $Matches[1] }
    return ""
}

$SlackToken      = Get-TfVar "slack_bot_token"
$SlackChannel    = Get-TfVar "slack_channel"
$MemoryId        = Get-TfVar "agentcore_memory_id"
$LangSmithApiKey = Get-TfVar "langsmith_api_key"  # LangSmith 트레이싱 — AgentCore에서 LangGraph 실행 시 트레이스 기록
$DdbTable       = (terraform output -raw dynamodb_incident_table).Trim()
$AmpEndpoint    = (terraform output -raw amp_endpoint).Trim() + "api/v1/"
# -replace '^https?://' — agents_aws.py가 url = f"https://{OS_ENDPOINT}/..." 로 직접 조합하므로 https:// 제거
$OsEndpoint     = (terraform output -raw opensearch_endpoint).Trim() -replace '^https?://', ''
# VPC 모드용 — Lambda와 동일한 SG/Subnet 사용 (OpenSearch 접근 허용 규칙 공유)
$PrivateSubnet  = (terraform output -raw private_subnet_id).Trim()
$LambdaSg       = (terraform output -raw lambda_sg_id).Trim()

# JSON 파일로 저장 (PowerShell 이스케이프 회피)
$Artifact = @{
    containerConfiguration = @{ containerUri = $ImageUri }
} | ConvertTo-Json -Depth 3 -Compress

# VPC 모드: AgentCore Runtime을 VPC 내부에서 실행 (OpenSearch VPC 전용 접근 가능)
# Lambda와 동일한 SG를 사용하므로 OpenSearch SG의 인그레스 규칙이 그대로 적용됨
# -f 포맷 연산자 사용 — 문자열 연결(+) 방식의 PowerShell 파싱 오류 회피
$Network = '{{"networkMode":"VPC","networkModeConfig":{{"securityGroups":["{0}"],"subnets":["{1}"]}}}}' -f $LambdaSg, $PrivateSubnet

# ANALYSIS_MODEL_ID: Collector + sub-agent(metrics/logs/infra) 모델 — Sonnet 4 (tool_use 안정성)
# WRITER_MODEL_ID는 기본값(Haiku) 사용 — 환경변수 미설정 시 graph_agent_with_memory.py 기본값 적용
$EnvVars = @{
    DYNAMODB_INCIDENT_TABLE = $DdbTable
    AMP_ENDPOINT            = $AmpEndpoint
    OPENSEARCH_ENDPOINT     = $OsEndpoint
    OPENSEARCH_USER         = "admin"
    OPENSEARCH_PASSWORD     = "Fkvk1234!"
    SLACK_BOT_TOKEN         = $SlackToken
    SLACK_CHANNEL           = $SlackChannel
    AWS_REGION_NAME         = $Region
    AGENTCORE_MEMORY_ID     = $MemoryId
    ANALYSIS_MODEL_ID       = "apac.anthropic.claude-sonnet-4-20250514-v1:0"
} | ConvertTo-Json -Compress

# 디버그: 파일 쓰기 전 변수값 확인 (VPC 모드 전환 후 오류 추적용)
Write-Host "PrivateSubnet: $PrivateSubnet"
Write-Host "LambdaSg     : $LambdaSg"
Write-Host "Network JSON : $Network"

# Set-Content 사용 — WriteAllText($PWD\...) 경로 해석 오류 회피, 상대경로로 안전하게 기록
Set-Content -Path "artifact.json" -Value $Artifact -Encoding ASCII
Set-Content -Path "network.json"  -Value $Network  -Encoding ASCII
Set-Content -Path "envvars.json"  -Value $EnvVars  -Encoding ASCII

# 런타임 존재 여부 확인
$ExistingRuntimes = aws bedrock-agentcore-control list-agent-runtimes --region $Region | ConvertFrom-Json
$Existing = $ExistingRuntimes.agentRuntimes | Where-Object { $_.agentRuntimeName -eq $RuntimeName }

if ($Existing) {
    # 기존 런타임 업데이트
    $RuntimeId = $Existing.agentRuntimeId
    Write-Host "Existing Runtime Found (ID: $RuntimeId) → Updating..."

    $Result = aws bedrock-agentcore-control update-agent-runtime `
        --agent-runtime-id $RuntimeId `
        --agent-runtime-artifact file://artifact.json `
        --role-arn $RoleArn `
        --network-configuration file://network.json `
        --environment-variables file://envvars.json `
        --region $Region `
        --output json | ConvertFrom-Json

    $NewArn = $Result.agentRuntimeArn
    Write-Host "AgentCore Runtime Updated: $NewArn"
} else {
    # 신규 런타임 생성
    Write-Host "Existing Runtime Not Found → Creating New..."

    $Result = aws bedrock-agentcore-control create-agent-runtime `
        --agent-runtime-name $RuntimeName `
        --agent-runtime-artifact file://artifact.json `
        --role-arn $RoleArn `
        --network-configuration file://network.json `
        --environment-variables file://envvars.json `
        --region $Region `
        --output json | ConvertFrom-Json

    $NewArn = $Result.agentRuntimeArn
    Write-Host "AgentCore Runtime Created: $NewArn"

    # terraform.tfvars 자동 업데이트
    $OldLine = $TfVars -split "`n" | Select-String "agentcore_runtime_arn"
    (Get-Content terraform.tfvars) -replace 'agentcore_runtime_arn\s*=\s*"[^"]*"', "agentcore_runtime_arn = `"$NewArn`"" |
        Set-Content terraform.tfvars
    Write-Host "terraform.tfvars updated → terraform apply needed"
}

# 임시 파일 정리
Remove-Item artifact.json, network.json, envvars.json -ErrorAction SilentlyContinue
