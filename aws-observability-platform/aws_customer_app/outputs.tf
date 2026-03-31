output "frontend_public_ip" {
  description = "Frontend EC2 public IP"
  value       = aws_instance.frontend.public_ip
}

output "backend_public_ip" {
  description = "Backend EC2 public IP"
  value       = aws_instance.backend.public_ip
}

output "rds_endpoint" {
  description = "RDS Postgres endpoint"
  value       = aws_db_instance.postgres.address
}

output "flask_url" {
  value = "http://${aws_instance.frontend.public_ip}:5001"
}

output "thymeleaf_url" {
  value = "http://${aws_instance.frontend.public_ip}:8090"
}

output "ssm_connect_frontend" {
  description = "Session Manager (CLI)"
  value       = "aws ssm start-session --target ${aws_instance.frontend.id} --region ${var.aws_region}"
}

output "ssm_connect_backend" {
  description = "Session Manager (CLI)"
  value       = "aws ssm start-session --target ${aws_instance.backend.id} --region ${var.aws_region}"
}

