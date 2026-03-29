# AWS Observability Platform with Bedrock
# Architecture: External OTel App (Cognito JWT) → Envoy → EC2 OTel Collector
#               → metrics: AMP / logs: OpenSearch(direct) / traces: OpenSearch(direct) / backup: S3
# Note: OSIS 제거 - OTel Collector가 opensearch exporter로 직접 전송 (SigV4)

terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.27.0"
    }
    time = {
      source  = "hashicorp/time"
      version = ">= 0.9.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "ObservabilityPlatform"
      Environment = var.environment
      ManagedBy   = "Terraform"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# ============================================================
# Modules
# ============================================================

module "networking" {
  source       = "./modules/networking"
  project_name = var.project_name
  aws_region   = var.aws_region
}

module "storage" {
  source       = "./modules/storage"
  project_name = var.project_name
  account_id   = data.aws_caller_identity.current.account_id
}

module "observability" {
  source       = "./modules/observability"
  project_name = var.project_name
  aws_region   = var.aws_region
  account_id   = data.aws_caller_identity.current.account_id

  private_subnet_id          = module.networking.private_subnet_id
  opensearch_sg_id           = module.networking.opensearch_sg_id
  opensearch_master_user     = var.opensearch_master_user
  opensearch_master_password = var.opensearch_master_password

  s3_logs_backup_id     = module.storage.logs_backup_id
  s3_logs_backup_arn    = module.storage.logs_backup_arn
  s3_traces_backup_id   = module.storage.traces_backup_id
  s3_traces_backup_arn  = module.storage.traces_backup_arn
  s3_metrics_backup_id  = module.storage.metrics_backup_id
  s3_metrics_backup_arn = module.storage.metrics_backup_arn
  s3_runbooks_arn       = module.storage.runbooks_arn
  s3_athena_results_id  = module.storage.athena_results_id
}

module "chatbot" {
  source       = "./modules/chatbot"
  project_name = var.project_name
  s3_deploy_id = module.storage.deploy_id
}

module "alerting" {
  source       = "./modules/alerting"
  project_name = var.project_name
  aws_region   = var.aws_region

  vpc_id            = module.networking.vpc_id
  opensearch_sg_id  = module.networking.opensearch_sg_id
  private_subnet_id = module.networking.private_subnet_id

  opensearch_arn      = module.observability.opensearch_arn
  opensearch_endpoint = module.observability.opensearch_endpoint
  amp_endpoint        = module.observability.amp_endpoint
  sns_topic_arn       = module.observability.sns_topic_arn

  s3_deploy_id  = module.storage.deploy_id
  s3_deploy_arn = module.storage.deploy_arn

  opensearch_master_user     = var.opensearch_master_user
  opensearch_master_password = var.opensearch_master_password
  slack_bot_token            = var.slack_bot_token
  slack_channel              = var.slack_channel
  slack_signing_secret       = var.slack_signing_secret
  agentcore_memory_id        = var.agentcore_memory_id
  agentcore_runtime_arn      = var.agentcore_runtime_arn

  # bedrock_agent_memory.tf에서 생성된 리소스 (순환 없음: 해당 리소스들은 module.alerting에 의존하지 않음)
  bedrock_agent_id        = aws_bedrockagent_agent.observability.id
  bedrock_agent_alias_id  = aws_bedrockagent_agent_alias.prod.agent_alias_id
  dynamodb_incident_table = aws_dynamodb_table.incident_ongoing.name
  langsmith_api_key       = var.langsmith_api_key  # LangSmith 트레이싱용 API 키
}

module "compute" {
  source       = "./modules/compute"
  project_name = var.project_name
  aws_region   = var.aws_region

  ec2_ami_id   = var.ec2_ami_id
  ec2_key_name = var.ec2_key_name
  ec2_key_path = var.ec2_key_path

  public_subnet_id     = module.networking.public_subnet_id
  otel_collector_sg_id = module.networking.otel_collector_sg_id

  amp_endpoint     = module.observability.amp_endpoint
  amp_workspace_id = module.observability.amp_workspace_id

  opensearch_endpoint        = module.observability.opensearch_endpoint
  opensearch_arn             = module.observability.opensearch_arn
  opensearch_master_user     = var.opensearch_master_user
  opensearch_master_password = var.opensearch_master_password

  s3_logs_backup_id    = module.storage.logs_backup_id
  s3_logs_backup_arn   = module.storage.logs_backup_arn
  s3_traces_backup_id  = module.storage.traces_backup_id
  s3_traces_backup_arn = module.storage.traces_backup_arn
  s3_metrics_backup_id  = module.storage.metrics_backup_id
  s3_metrics_backup_arn = module.storage.metrics_backup_arn
  s3_athena_results_id  = module.storage.athena_results_id
  s3_athena_results_arn = module.storage.athena_results_arn
  s3_deploy_id          = module.storage.deploy_id
  s3_deploy_arn         = module.storage.deploy_arn

  cognito_user_pool_id = module.observability.cognito_user_pool_id
  cognito_client_id    = module.observability.cognito_client_id

  agentcore_memory_id = var.agentcore_memory_id

  chatbot_conversations_table = module.chatbot.conversations_table
  chatbot_messages_table      = module.chatbot.messages_table
}
