variable "project_name" {
  description = "Project name for resource naming"
  type        = string
}

variable "aws_region" {
  description = "AWS Region"
  type        = string
}

variable "account_id" {
  description = "AWS Account ID"
  type        = string
}

variable "private_subnet_id" {
  description = "Private subnet ID for OpenSearch VPC placement"
  type        = string
}

variable "opensearch_sg_id" {
  description = "Security group ID for OpenSearch"
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
  description = "S3 logs backup bucket ID"
  type        = string
}

variable "s3_logs_backup_arn" {
  description = "S3 logs backup bucket ARN"
  type        = string
}

variable "s3_traces_backup_id" {
  description = "S3 traces backup bucket ID"
  type        = string
}

variable "s3_traces_backup_arn" {
  description = "S3 traces backup bucket ARN"
  type        = string
}

variable "s3_metrics_backup_id" {
  description = "S3 metrics backup bucket ID"
  type        = string
}

variable "s3_metrics_backup_arn" {
  description = "S3 metrics backup bucket ARN"
  type        = string
}

variable "s3_runbooks_arn" {
  description = "S3 runbooks bucket ARN"
  type        = string
}

variable "s3_athena_results_id" {
  description = "S3 Athena results bucket ID"
  type        = string
}
