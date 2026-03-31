variable "project_name" {
  description = "프로젝트 이름 (리소스 네이밍에 사용)"
  type        = string
  default     = "log-platform-dev"
}

variable "aws_region" {
  description = "AWS 리전"
  type        = string
  default     = "ap-northeast-2"
}

variable "ec2_ami_id" {
  description = "EC2 AMI ID (Ubuntu 22.04)"
  type        = string
  # ap-northeast-2 Ubuntu 22.04 LTS: ami-042e76978adeb8c48
}

variable "environment" {
  description = "OTel resource attribute deployment.environment 값"
  type        = string
  default     = "prod"
}

variable "otel_gateway_endpoint" {
  description = "플랫폼 게이트웨이 OTLP gRPC (NLB DNS:4317). 예: xxx.elb.ap-northeast-2.amazonaws.com:4317"
  type        = string
}

