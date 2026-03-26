output "lambda_agent_role_id" {
  description = "Lambda Agent IAM Role ID (bedrock_agent_memory, agentcore_*.tf에서 policy 추가용)"
  value       = aws_iam_role.lambda_agent.id
}

output "lambda_agent_role_name" {
  value = aws_iam_role.lambda_agent.name
}

output "sns_topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "agent_function_url" {
  value = aws_lambda_function_url.agent.function_url
}
