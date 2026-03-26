output "amp_workspace_id" {
  value = aws_prometheus_workspace.main.id
}

output "amp_endpoint" {
  value = aws_prometheus_workspace.main.prometheus_endpoint
}

output "opensearch_endpoint" {
  value = aws_opensearch_domain.main.endpoint
}

output "opensearch_arn" {
  value = aws_opensearch_domain.main.arn
}

output "bedrock_agent_role_arn" {
  value = aws_iam_role.bedrock_agent.arn
}

output "glue_database_name" {
  value = aws_glue_catalog_database.observability.name
}

output "athena_workgroup_name" {
  value = aws_athena_workgroup.observability.name
}

output "cognito_user_pool_id" {
  value = aws_cognito_user_pool.otel_auth.id
}

output "cognito_client_id" {
  value = aws_cognito_user_pool_client.otel_customer.id
}

output "cognito_client_secret" {
  value     = aws_cognito_user_pool_client.otel_customer.client_secret
  sensitive = true
}

output "cognito_token_endpoint" {
  value = "https://${aws_cognito_user_pool_domain.otel_auth.domain}.auth.${var.aws_region}.amazoncognito.com/oauth2/token"
}

output "cognito_jwks_uri" {
  value = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.otel_auth.id}/.well-known/jwks.json"
}
