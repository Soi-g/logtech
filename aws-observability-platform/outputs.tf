output "otel_collector_public_ip" {
  description = "우리 OTel Collector EC2 Public IP (LFS148 앱이 이 주소로 데이터를 전송)"
  value       = module.compute.otel_collector_public_ip
}

output "otel_collector_otlp_grpc" {
  description = "OTLP gRPC 엔드포인트 (LFS148 앱 OTel Collector exporter에 설정)"
  value       = "${module.compute.otel_collector_public_ip}:4317"
}

output "otel_collector_otlp_http" {
  description = "OTLP HTTP 엔드포인트 (LFS148 앱 OTel Collector exporter에 설정)"
  value       = "http://${module.compute.otel_collector_public_ip}:4318"
}

output "amp_workspace_id" {
  description = "Amazon Managed Prometheus Workspace ID"
  value       = module.observability.amp_workspace_id
}

output "amp_endpoint" {
  description = "AMP 엔드포인트 (메트릭 저장)"
  value       = module.observability.amp_endpoint
}

output "opensearch_endpoint" {
  description = "OpenSearch 도메인 엔드포인트 (로그/트레이스 저장)"
  value       = "https://${module.observability.opensearch_endpoint}"
}

output "opensearch_dashboard_url" {
  description = "OpenSearch 대시보드 URL"
  value       = "https://${module.observability.opensearch_endpoint}/_dashboards"
}

output "otel_collector_role_arn" {
  description = "OTel Collector EC2 IAM Role ARN (OpenSearch 직접 쓰기용 - SigV4)"
  value       = module.compute.otel_collector_role_arn
}

output "s3_logs_backup_bucket" {
  description = "로그 S3 백업 버킷 이름"
  value       = module.storage.logs_backup_id
}

output "s3_traces_backup_bucket" {
  description = "트레이스 S3 백업 버킷 이름"
  value       = module.storage.traces_backup_id
}

output "s3_metrics_backup_bucket" {
  description = "메트릭 S3 백업 버킷 이름"
  value       = module.storage.metrics_backup_id
}

output "s3_runbooks_bucket" {
  description = "Bedrock 런북 S3 버킷 이름"
  value       = module.storage.runbooks_id
}

output "s3_deploy_bucket" {
  description = "배포 아티팩트 S3 버킷 (Lambda 코드, 챗봇 패키지)"
  value       = module.storage.deploy_id
}

output "bedrock_agent_role_arn" {
  description = "Bedrock Agent IAM Role ARN"
  value       = module.observability.bedrock_agent_role_arn
}

output "ssh_command" {
  description = "EC2 SSH 접속 명령어"
  value       = "ssh -i ${var.ec2_key_path} ubuntu@${module.compute.otel_collector_public_ip}"
}

output "athena_workgroup" {
  description = "Athena 워크그룹 이름"
  value       = module.observability.athena_workgroup_name
}

output "glue_database" {
  description = "Glue 카탈로그 데이터베이스 이름"
  value       = module.observability.glue_database_name
}

output "cognito_user_pool_id" {
  description = "Cognito User Pool ID"
  value       = module.observability.cognito_user_pool_id
}

output "cognito_client_id" {
  description = "고객 OTel Collector용 Client ID"
  value       = module.observability.cognito_client_id
}

output "cognito_client_secret" {
  description = "고객 OTel Collector용 Client Secret"
  value       = module.observability.cognito_client_secret
  sensitive   = true
}

output "cognito_token_endpoint" {
  description = "고객 OTel Collector가 토큰 발급받는 엔드포인트"
  value       = module.observability.cognito_token_endpoint
}

output "cognito_jwks_uri" {
  description = "Envoy가 토큰 검증하는 JWKS URI"
  value       = module.observability.cognito_jwks_uri
}

output "next_steps" {
  description = "배포 후 다음 단계"
  sensitive   = true
  value       = <<-EOT

  ========================================
  인프라 배포 완료!
  ========================================

  [인증 구조]
  고객 OTel → Envoy(4317/4318) → JWT 검증(Cognito) → OTelCol(14317/14318)

  [Step 1] 토큰 발급 (PowerShell)
  terraform output -raw cognito_client_secret
  → CLIENT_ID / CLIENT_SECRET 확인 후 토큰 발급

  [Step 2] LFS148 otel-collector-config.yml 설정
  oauth2client extension에 client_id/secret/token_url 설정

  [Step 3] Envoy 상태 확인
  ssh ubuntu@${module.compute.otel_collector_public_ip}
  docker logs envoy -f

  [Step 4] OTel Collector 상태 확인
  sudo systemctl status otelcol
  sudo journalctl -u otelcol -f

  ========================================
  EOT
}

output "sns_topic_arn" {
  description = "SNS 알람 토픽 ARN"
  value       = module.observability.sns_topic_arn
}

output "agent_function_url" {
  description = "Lambda 에이전트 Function URL"
  value       = module.alerting.agent_function_url
}

# AgentCore Runtime VPC 모드 설정용 — build_agentcore.ps1에서 terraform output으로 참조
output "private_subnet_id" {
  description = "Private Subnet ID — AgentCore Runtime을 VPC 모드로 실행할 때 사용"
  value       = module.networking.private_subnet_id
}

output "lambda_sg_id" {
  description = "Lambda Security Group ID — AgentCore Runtime VPC 모드에서 재사용 (OpenSearch 접근 허용 규칙 공유)"
  value       = module.alerting.lambda_sg_id
}
