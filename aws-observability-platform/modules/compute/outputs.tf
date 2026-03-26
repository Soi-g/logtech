output "otel_collector_public_ip" {
  value = aws_eip.otel_collector.public_ip
}

output "otel_collector_role_arn" {
  value = aws_iam_role.otel_collector.arn
}

output "otel_collector_eip" {
  value = aws_eip.otel_collector.public_ip
}
