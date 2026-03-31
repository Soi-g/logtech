output "vpc_id" {
  value = aws_vpc.main.id
}

output "vpc_cidr_block" {
  value = aws_vpc.main.cidr_block
}

output "public_subnet_id" {
  value = aws_subnet.public.id
}

output "public_subnet_ids" {
  description = "퍼블릭 서브넷 (NLB/ASG용, 서로 다른 AZ)"
  value       = [aws_subnet.public.id, aws_subnet.public_b.id]
}

output "private_subnet_id" {
  value = aws_subnet.private.id
}

output "otel_collector_sg_id" {
  value = aws_security_group.otel_collector.id
}

output "opensearch_sg_id" {
  value = aws_security_group.opensearch.id
}
