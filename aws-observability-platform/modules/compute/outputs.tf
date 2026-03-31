output "otel_gateway_nlb_dns" {
  description = "Internet-facing NLB DNS — agents use :4317 (gRPC) / :4318 (HTTP) → collector 14317/14318"
  value       = aws_lb.otel_gateway.dns_name
}

output "otel_gateway_nlb_zone_id" {
  value = aws_lb.otel_gateway.zone_id
}

output "otel_gateway_asg_name" {
  value = aws_autoscaling_group.otel_gateway.name
}

output "otel_collector_public_ip" {
  description = "Deprecated: 단일 EIP 제거. 접속은 Session Manager (terraform output ssm_gateway_hint)"
  value       = null
}

output "otel_collector_role_arn" {
  value = aws_iam_role.otel_collector.arn
}

output "otel_collector_eip" {
  description = "Deprecated: NLB 사용"
  value       = null
}
