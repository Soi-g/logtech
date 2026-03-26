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

variable "amp_workspace_id" {
  type = string
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
