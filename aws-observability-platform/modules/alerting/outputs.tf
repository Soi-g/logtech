output "lambda_agent_role_id" {
  description = "Lambda Agent IAM Role ID (bedrock_agent_memory, agentcore_*.tf에서 policy 추가용)"
  value       = aws_iam_role.lambda_agent.id
}

output "lambda_agent_role_name" {
  value = aws_iam_role.lambda_agent.name
}

output "agent_function_url" {
  value = aws_lambda_function_url.agent.function_url
}

# AgentCore Runtime VPC 모드 설정 시 동일 SG 사용 (OpenSearch 접근 허용 규칙 공유)
output "lambda_sg_id" {
  description = "Lambda Security Group ID — AgentCore Runtime VPC 모드에서 재사용"
  value       = aws_security_group.lambda.id
}
