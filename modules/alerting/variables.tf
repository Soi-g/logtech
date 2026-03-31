variable "project_name" {
  type = string
}

variable "aws_region" {
  type = string
}

# Networking
variable "vpc_id" {
  type = string
}

variable "opensearch_sg_id" {
  type = string
}

variable "private_subnet_id" {
  type = string
}

# Observability
variable "opensearch_arn" {
  type = string
}

variable "opensearch_endpoint" {
  description = "OpenSearch domain endpoint (hostname only, no https://)"
  type        = string
}

variable "amp_endpoint" {
  type = string
}

variable "sns_topic_arn" {
  description = "SNS Topic ARN for Lambda subscription (from observability module)"
  type        = string
}

# Storage
variable "s3_deploy_id" {
  type = string
}

variable "s3_deploy_arn" {
  type = string
}

# Credentials
variable "opensearch_master_user" {
  type = string
}

variable "opensearch_master_password" {
  type      = string
  sensitive = true
}

variable "slack_bot_token" {
  type      = string
  sensitive = true
}

variable "slack_channel" {
  type = string
}

variable "slack_signing_secret" {
  type      = string
  sensitive = true
  default   = ""
}

variable "agentcore_memory_id" {
  type    = string
  default = ""
}

variable "agentcore_runtime_arn" {
  type    = string
  default = ""
}

# Bedrock Agent (bedrock_agent_memory.tf에서 생성된 후 전달)
variable "bedrock_agent_id" {
  type = string
}

variable "bedrock_agent_alias_id" {
  type = string
}

variable "dynamodb_incident_table" {
  type = string
}

# LangSmith — Lambda에서 LangGraph 실행 시 노드별 입출력, 툴 호출, 소요 시간을 UI로 기록
variable "langsmith_api_key" {
  description = "LangSmith API 키 — LangGraph 실행 트레이싱용"
  type        = string
  sensitive   = true
  default     = ""
}
