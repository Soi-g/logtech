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

output "ssh_frontend" {
  value = "ssh -i ${var.ec2_key_name}.pem ubuntu@${aws_instance.frontend.public_ip}"
}

output "ssh_backend" {
  value = "ssh -i ${var.ec2_key_name}.pem ubuntu@${aws_instance.backend.public_ip}"
}

