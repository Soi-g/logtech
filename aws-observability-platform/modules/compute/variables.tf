variable "project_name" {
  description = "Project name for resource naming"
  type        = string
}

variable "aws_region" {
  description = "AWS Region"
  type        = string
}

variable "ec2_ami_id" {
  description = "AMI ID for EC2 instance"
  type        = string
}

variable "public_subnet_ids" {
  description = "Public subnet IDs for gateway ASG / NLB (at least 2 AZs)"
  type        = list(string)
}

variable "vpc_id" {
  description = "VPC ID for NLB target groups"
  type        = string
}

variable "otel_collector_sg_id" {
  description = "Security group ID for OTel Collector EC2"
  type        = string
}

variable "amp_endpoint" {
  description = "AMP prometheus endpoint URL"
  type        = string
}

variable "amp_workspace_id" {
  description = "AMP workspace ID"
  type        = string
}

variable "opensearch_endpoint" {
  description = "OpenSearch domain endpoint"
  type        = string
}

variable "opensearch_arn" {
  description = "OpenSearch domain ARN"
  type        = string
}

variable "opensearch_master_user" {
  description = "OpenSearch master username"
  type        = string
}

variable "opensearch_master_password" {
  description = "OpenSearch master password"
  type        = string
  sensitive   = true
}

variable "s3_logs_backup_id" {
  type = string
}

variable "s3_logs_backup_arn" {
  type = string
}

variable "s3_traces_backup_id" {
  type = string
}

variable "s3_traces_backup_arn" {
  type = string
}

variable "s3_metrics_backup_id" {
  type = string
}

variable "s3_metrics_backup_arn" {
  type = string
}

variable "s3_athena_results_id" {
  type = string
}

variable "s3_athena_results_arn" {
  type = string
}

variable "s3_deploy_id" {
  type = string
}

variable "s3_deploy_arn" {
  type = string
}

variable "cognito_user_pool_id" {
  type = string
}

variable "cognito_client_id" {
  type = string
}

variable "agentcore_memory_id" {
  description = "Bedrock AgentCore Memory Store ID"
  type        = string
  default     = ""
}

variable "chatbot_conversations_table" {
  description = "DynamoDB chatbot conversations table name"
  type        = string
}

variable "chatbot_messages_table" {
  description = "DynamoDB chatbot messages table name"
  type        = string
}

