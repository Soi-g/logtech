# AgentCore Runtime 빌드 및 배포 스크립트 (Windows PowerShell)
# terraform apply 후 실행: .\build_agentcore.ps1

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
docker build --platform linux/arm64 -t "${RuntimeName}:latest" -f Dockerfile .

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

$SlackToken   = Get-TfVar "slack_bot_token"
$SlackChannel = Get-TfVar "slack_channel"
$MemoryId     = Get-TfVar "agentcore_memory_id"
$DdbTable     = (terraform output -raw dynamodb_incident_table).Trim()
$AmpEndpoint  = (terraform output -raw amp_endpoint).Trim() + "api/v1/"
$OsEndpoint   = (terraform output -raw opensearch_endpoint).Trim()

# JSON 파일로 저장 (PowerShell 이스케이프 회피)
$Artifact = @{
    containerConfiguration = @{ containerUri = $ImageUri }
} | ConvertTo-Json -Depth 3 -Compress

$Network = @{ networkMode = "PUBLIC" } | ConvertTo-Json -Compress

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
} | ConvertTo-Json -Compress

[System.IO.File]::WriteAllText("$PWD\artifact.json", $Artifact, [System.Text.Encoding]::ASCII)
[System.IO.File]::WriteAllText("$PWD\network.json",  $Network,  [System.Text.Encoding]::ASCII)
[System.IO.File]::WriteAllText("$PWD\envvars.json",  $EnvVars,  [System.Text.Encoding]::ASCII)

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
