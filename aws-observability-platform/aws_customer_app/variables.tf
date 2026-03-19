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

variable "ec2_key_name" {
  description = "EC2 Key Pair 이름 (SSH 접속용)"
  type        = string
}

variable "environment" {
  description = "OTel resource attribute deployment.environment 값"
  type        = string
  default     = "prod"
}

