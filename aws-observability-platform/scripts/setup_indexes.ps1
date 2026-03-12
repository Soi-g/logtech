# AOSS 인덱스 생성 스크립트 (Windows PowerShell)
# terraform apply 완료 후 실행

param(
    [Parameter(Mandatory=$true)]
    [string]$IncidentMemoryEndpoint,
    
    [Parameter(Mandatory=$true)]
    [string]$RunbooksEndpoint,
    
    [string]$Region = "ap-northeast-2",
    [string]$ProjectName = "log-platform-dev"
)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "AOSS 인덱스 생성 시작" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# 1. Incident Memory 인덱스 생성
Write-Host "`n[1/2] Incident Memory 인덱스 생성 중..." -ForegroundColor Yellow

$incidentPayload = @{
    endpoint = $IncidentMemoryEndpoint
    region = $Region
    index_name = "incident-memory-index"
} | ConvertTo-Json

$incidentPayload | Out-File -FilePath "incident_memory_payload.json" -Encoding utf8

Write-Host "  대기 중 (60초)..." -ForegroundColor Gray
Start-Sleep -Seconds 60

aws lambda invoke `
    --function-name "$ProjectName-incident-memory-index-creator" `
    --region $Region `
    --payload file://incident_memory_payload.json `
    incident_memory_response.json

if ($LASTEXITCODE -eq 0) {
    Write-Host "  ✅ Incident Memory 인덱스 생성 완료" -ForegroundColor Green
    Get-Content incident_memory_response.json
} else {
    Write-Host "  ❌ Incident Memory 인덱스 생성 실패" -ForegroundColor Red
}

# 2. Knowledge Base 인덱스 생성
Write-Host "`n[2/2] Knowledge Base 인덱스 생성 중..." -ForegroundColor Yellow

$kbPayload = @{
    endpoint = $RunbooksEndpoint
    region = $Region
    index_name = "bedrock-knowledge-base-default-index"
} | ConvertTo-Json

$kbPayload | Out-File -FilePath "kb_index_payload.json" -Encoding utf8

Write-Host "  대기 중 (30초)..." -ForegroundColor Gray
Start-Sleep -Seconds 30

aws lambda invoke `
    --function-name "$ProjectName-aoss-index-creator" `
    --region $Region `
    --payload file://kb_index_payload.json `
    kb_index_response.json

if ($LASTEXITCODE -eq 0) {
    Write-Host "  ✅ Knowledge Base 인덱스 생성 완료" -ForegroundColor Green
    Get-Content kb_index_response.json
} else {
    Write-Host "  ❌ Knowledge Base 인덱스 생성 실패" -ForegroundColor Red
}

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "인덱스 생성 완료!" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# 정리
Remove-Item incident_memory_payload.json -ErrorAction SilentlyContinue
Remove-Item kb_index_payload.json -ErrorAction SilentlyContinue
