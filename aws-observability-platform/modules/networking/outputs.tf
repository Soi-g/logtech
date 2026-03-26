output "vpc_id" {
  value = aws_vpc.main.id
}

output "vpc_cidr_block" {
  value = aws_vpc.main.cidr_block
}

output "public_subnet_id" {
  value = aws_subnet.public.id
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
