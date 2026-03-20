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
# VPC / Network
# ============================================================

resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags                 = { Name = "${var.project_name}-vpc" }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.1.0/24"
  availability_zone       = "${var.aws_region}a"
  map_public_ip_on_launch = true
  tags                    = { Name = "${var.project_name}-public-subnet" }
}

resource "aws_subnet" "private" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.2.0/24"
  availability_zone = "${var.aws_region}a"
  tags              = { Name = "${var.project_name}-private-subnet" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${var.project_name}-igw" }
}

# NAT Gateway용 Elastic IP
resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = { Name = "${var.project_name}-nat-eip" }
}

# NAT Gateway (Public Subnet에 배치)
resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public.id
  tags          = { Name = "${var.project_name}-nat-gw" }
  depends_on    = [aws_internet_gateway.main]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = { Name = "${var.project_name}-public-rt" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# Private Route Table (NAT Gateway 통해 인터넷 접근)
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }
  tags = { Name = "${var.project_name}-private-rt" }
}

resource "aws_route_table_association" "private" {
  subnet_id      = aws_subnet.private.id
  route_table_id = aws_route_table.private.id
}

# ============================================================
# Security Groups
# ============================================================

resource "aws_security_group" "otel_collector" {
  name        = "${var.project_name}-otel-collector-sg"
  description = "Receives OTLP from external OTel Collectors (LFS148 apps)"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "OTLP gRPC - from external OTel Collector (via Envoy JWT auth)"
    from_port   = 4317
    to_port     = 4317
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "OTLP gRPC direct - from trusted OTel Agents (bypass Envoy)"
    from_port   = 14317
    to_port     = 14317
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "OTLP HTTP - from external OTel Collector"
    from_port   = 4318
    to_port     = 4318
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Grafana"
    from_port   = 3000
    to_port     = 3000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Dev ports (3001, 8081-8084)"
    from_port   = 3001
    to_port     = 3001
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Dev ports (3001, 8081-8084)"
    from_port   = 8081
    to_port     = 8084
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Chatbot"
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project_name}-otel-sg" }
}

resource "aws_security_group" "opensearch" {
  name        = "${var.project_name}-opensearch-sg"
  description = "OpenSearch access from VPC only"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.main.cidr_block]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project_name}-opensearch-sg" }
}

# ============================================================
# IAM - EC2 OTel Collector
# ============================================================

resource "aws_iam_role" "otel_collector" {
  name = "${var.project_name}-otel-collector-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "otel_collector" {
  name = "${var.project_name}-otel-collector-policy"
  role = aws_iam_role.otel_collector.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AMPAccess"
        Effect = "Allow"
        Action = ["aps:RemoteWrite", "aps:QueryMetrics", "aps:GetSeries", "aps:GetLabels", "aps:GetMetricMetadata"]
        Resource = "*"
      },
      {
        Sid    = "S3Write"
        Effect = "Allow"
        Action = ["s3:PutObject", "s3:PutObjectAcl"]
        Resource = [
          "${aws_s3_bucket.logs_backup.arn}/*",
          "${aws_s3_bucket.traces_backup.arn}/*",
          "${aws_s3_bucket.metrics_backup.arn}/*"
        ]
      },
      {
        Sid    = "S3AthenaAccess"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:ListBucket", "s3:PutObject", "s3:GetBucketLocation"]
        Resource = [
          "${aws_s3_bucket.athena_results.arn}",
          "${aws_s3_bucket.athena_results.arn}/*",
          "${aws_s3_bucket.logs_backup.arn}",
          "${aws_s3_bucket.logs_backup.arn}/*",
          "${aws_s3_bucket.traces_backup.arn}",
          "${aws_s3_bucket.traces_backup.arn}/*"
        ]
      },
      {
        Sid    = "S3DeployRead"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:ListBucket"]
        Resource = [
          "${aws_s3_bucket.deploy.arn}",
          "${aws_s3_bucket.deploy.arn}/*"
        ]
      },
      {
        Sid    = "OpenSearchAccess"
        Effect = "Allow"
        Action = ["es:ESHttp*"]
        Resource = [
          aws_opensearch_domain.main.arn,
          "${aws_opensearch_domain.main.arn}/*"
        ]
      },
      {
        Sid    = "CloudWatchAccess"
        Effect = "Allow"
        Action = [
          "cloudwatch:PutMetricData",
          "cloudwatch:GetMetricData",
          "cloudwatch:GetMetricStatistics",
          "cloudwatch:ListMetrics",
          "cloudwatch:DescribeAlarms",
          "logs:FilterLogEvents",
          "logs:GetLogEvents",
          "logs:DescribeLogGroups",
          "logs:DescribeLogStreams"
        ]
        Resource = "*"
      },
      {
        Sid    = "EC2Access"
        Effect = "Allow"
        Action = ["ec2:DescribeInstances", "ec2:DescribeInstanceStatus"]
        Resource = "*"
      },
      {
        Sid    = "RDSAccess"
        Effect = "Allow"
        Action = ["rds:DescribeDBInstances", "rds:DescribeDBClusters"]
        Resource = "*"
      },
      {
        Sid    = "ELBAccess"
        Effect = "Allow"
        Action = [
          "elasticloadbalancing:DescribeLoadBalancers",
          "elasticloadbalancing:DescribeTargetGroups",
          "elasticloadbalancing:DescribeTargetHealth"
        ]
        Resource = "*"
      },
      {
        Sid    = "AutoScalingAccess"
        Effect = "Allow"
        Action = ["autoscaling:DescribeAutoScalingGroups", "autoscaling:DescribeScalingActivities"]
        Resource = "*"
      },
      {
        Sid    = "CloudTrailAccess"
        Effect = "Allow"
        Action = ["cloudtrail:LookupEvents"]
        Resource = "*"
      },
      {
        Sid    = "DynamoDBAccess"
        Effect = "Allow"
        Action = ["dynamodb:Scan", "dynamodb:GetItem", "dynamodb:Query"]
        Resource = "arn:aws:dynamodb:${var.aws_region}:*:table/${var.project_name}-*"
      },
      {
        Sid    = "BedrockAccess"
        Effect = "Allow"
        Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = "*"
      },
      {
        Sid    = "BedrockAgentCoreAccess"
        Effect = "Allow"
        Action = [
          "bedrock-agent-runtime:RetrieveAndGenerate",
          "bedrock-agent-runtime:InvokeAgent",
          "bedrock-agent-runtime:Retrieve"
        ]
        Resource = "*"
      },
      {
        Sid    = "AthenaAccess"
        Effect = "Allow"
        Action = [
          "athena:StartQueryExecution",
          "athena:GetQueryExecution",
          "athena:GetQueryResults",
          "athena:StopQueryExecution",
          "athena:ListQueryExecutions"
        ]
        Resource = "*"
      },
      {
        Sid    = "GlueAccess"
        Effect = "Allow"
        Action = ["glue:GetTable", "glue:GetTables", "glue:GetDatabase", "glue:GetPartitions"]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_instance_profile" "otel_collector" {
  name = "${var.project_name}-otel-collector-profile"
  role = aws_iam_role.otel_collector.name
}

# ============================================================
# IAM - Bedrock Agent
# ============================================================

resource "aws_iam_role" "bedrock_agent" {
  name = "${var.project_name}-bedrock-agent-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "bedrock.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "bedrock_agent" {
  name = "${var.project_name}-bedrock-agent-policy"
  role = aws_iam_role.bedrock_agent.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["aps:QueryMetrics", "aps:GetSeries", "aps:GetLabels"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["es:ESHttpGet", "es:ESHttpPost"]
        Resource = "${aws_opensearch_domain.main.arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:ListBucket"]
        Resource = [aws_s3_bucket.runbooks.arn, "${aws_s3_bucket.runbooks.arn}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock:Retrieve"]
        Resource = "*"
      }
    ]
  })
}

# ============================================================
# EC2 - 우리 시스템의 OTel Collector
# ============================================================

# ============================================================
# 챗봇 코드 패키징 → S3 업로드 (EC2 부팅 시 다운로드)
# archive_file로 zip 생성 (셸 명령어 불필요, Windows/Linux 공통)
# ============================================================
data "archive_file" "chatbot" {
  type        = "zip"
  output_path = "${path.module}/chatbot_package.zip"

  source {
    content  = file("${path.module}/chatbot/app.py")
    filename = "chatbot/app.py"
  }
  source {
    content  = file("${path.module}/chatbot/chat_agent.py")
    filename = "chatbot/chat_agent.py"
  }
  source {
    content  = file("${path.module}/chatbot/database.py")
    filename = "chatbot/database.py"
  }
  source {
    content  = file("${path.module}/chatbot/requirements.txt")
    filename = "chatbot/requirements.txt"
  }
  source {
    content  = file("${path.module}/chatbot/templates/index.html")
    filename = "chatbot/templates/index.html"
  }
  source {
    content  = file("${path.module}/lambda_package/agents_aws.py")
    filename = "lambda_package/agents_aws.py"
  }
  source {
    content  = file("${path.module}/lambda_package/agentcore_memory.py")
    filename = "lambda_package/agentcore_memory.py"
  }
}

resource "aws_s3_object" "chatbot_package" {
  bucket = aws_s3_bucket.deploy.id
  key    = "chatbot/chatbot.zip"
  source = data.archive_file.chatbot.output_path
  etag   = data.archive_file.chatbot.output_md5

  depends_on = [aws_s3_bucket.deploy]
}

resource "aws_instance" "otel_collector" {
  ami                         = var.ec2_ami_id
  instance_type               = "t2.micro"
  subnet_id                   = aws_subnet.public.id
  vpc_security_group_ids      = [aws_security_group.otel_collector.id]
  iam_instance_profile        = aws_iam_instance_profile.otel_collector.name
  key_name                    = var.ec2_key_name
  user_data_replace_on_change = true  # user_data.sh 변경 시 자동으로 EC2 교체

  root_block_device {
    volume_size = 8
    volume_type = "gp3"
  }

  user_data_base64 = base64gzip(templatefile("${path.module}/user_data.sh", {
    amp_remote_write_url       = aws_prometheus_workspace.main.prometheus_endpoint
    amp_workspace_id           = aws_prometheus_workspace.main.id
    aws_region                 = var.aws_region
    s3_logs_bucket             = aws_s3_bucket.logs_backup.id
    s3_traces_bucket           = aws_s3_bucket.traces_backup.id
    s3_metrics_bucket          = aws_s3_bucket.metrics_backup.id
    project_name               = var.project_name
    project_name_underscore    = replace(var.project_name, "-", "_")
    cognito_user_pool_id       = aws_cognito_user_pool.otel_auth.id
    cognito_client_id          = aws_cognito_user_pool_client.otel_customer.id
    opensearch_endpoint        = aws_opensearch_domain.main.endpoint
    opensearch_master_user     = var.opensearch_master_user
    opensearch_master_password = var.opensearch_master_password
    otel_collector_role_arn    = aws_iam_role.otel_collector.arn
    agentcore_memory_id        = var.agentcore_memory_id
    athena_results_bucket      = aws_s3_bucket.athena_results.id
    logs_bucket                = aws_s3_bucket.logs_backup.id
    traces_bucket              = aws_s3_bucket.traces_backup.id
    deploy_bucket              = aws_s3_bucket.deploy.id
  }))
  tags = { Name = "${var.project_name}-otel-collector" }

  depends_on = [
    aws_prometheus_workspace.main,
    aws_opensearch_domain.main,
    aws_s3_bucket.logs_backup,
    aws_s3_bucket.traces_backup,
    aws_s3_bucket.metrics_backup,
    aws_s3_bucket.athena_results,
    aws_s3_bucket.deploy,
    aws_s3_object.chatbot_package,
  ]
}


resource "aws_eip" "otel_collector" {
  instance = aws_instance.otel_collector.id
  domain   = "vpc"
  tags     = { Name = "${var.project_name}-otel-collector-eip" }
}

# ============================================================
# Amazon Managed Prometheus (AMP)
# ============================================================

resource "aws_prometheus_workspace" "main" {
  alias = "${var.project_name}-amp"
  tags  = { Name = "${var.project_name}-amp" }
}

# Prometheus 기록 규칙 (v2에서 이식 - app/infra/jvm derived metrics)
resource "aws_prometheus_rule_group_namespace" "derived_metrics" {
  name         = "derived-metrics"
  workspace_id = aws_prometheus_workspace.main.id
  data         = file("${path.module}/prometheus-rules.yaml")
}

# ============================================================
# S3 Buckets
# ============================================================

resource "aws_s3_bucket" "logs_backup" {
  bucket = "${var.project_name}-logs-backup-${data.aws_caller_identity.current.account_id}"
  tags   = { Name = "${var.project_name}-logs-backup" }
}

resource "aws_s3_bucket_lifecycle_configuration" "logs_backup" {
  bucket = aws_s3_bucket.logs_backup.id
  rule {
    id     = "delete-old-logs"
    status = "Enabled"
    filter { prefix = "" }
    expiration { days = 30 }
  }
}

resource "aws_s3_bucket" "traces_backup" {
  bucket = "${var.project_name}-traces-backup-${data.aws_caller_identity.current.account_id}"
  tags   = { Name = "${var.project_name}-traces-backup" }
}

resource "aws_s3_bucket_lifecycle_configuration" "traces_backup" {
  bucket = aws_s3_bucket.traces_backup.id
  rule {
    id     = "delete-old-traces"
    status = "Enabled"
    filter { prefix = "" }
    expiration { days = 30 }
  }
}

resource "aws_s3_bucket" "metrics_backup" {
  bucket = "${var.project_name}-metrics-backup-${data.aws_caller_identity.current.account_id}"
  tags   = { Name = "${var.project_name}-metrics-backup" }
}

resource "aws_s3_bucket_lifecycle_configuration" "metrics_backup" {
  bucket = aws_s3_bucket.metrics_backup.id
  rule {
    id     = "expire-old-metrics"
    status = "Enabled"
    expiration { days = 365 }
  }
}

resource "aws_s3_bucket" "runbooks" {
  bucket = "${var.project_name}-runbooks-${data.aws_caller_identity.current.account_id}"
  tags   = { Name = "${var.project_name}-runbooks" }
}

# 배포 전용 버킷 — Lambda 코드, chatbot 패키지 등 배포 아티팩트 전용
resource "aws_s3_bucket" "deploy" {
  bucket = "${var.project_name}-deploy-${data.aws_caller_identity.current.account_id}"
  tags   = { Name = "${var.project_name}-deploy" }
}

resource "aws_s3_bucket_versioning" "deploy" {
  bucket = aws_s3_bucket.deploy.id
  versioning_configuration { status = "Enabled" }
}


# ============================================================
# OpenSearch Domain
# ============================================================

resource "aws_opensearch_domain" "main" {
  domain_name    = var.project_name
  engine_version = "OpenSearch_2.11"

  cluster_config {
    instance_type  = "t3.small.search"
    instance_count = 1
  }

  ebs_options {
    ebs_enabled = true
    volume_size = 10
    volume_type = "gp3"
  }

  vpc_options {
    subnet_ids         = [aws_subnet.private.id]
    security_group_ids = [aws_security_group.opensearch.id]
  }

  advanced_security_options {
    enabled                        = true
    internal_user_database_enabled = true
    master_user_options {
      master_user_name     = var.opensearch_master_user
      master_user_password = var.opensearch_master_password
    }
  }

  encrypt_at_rest { enabled = true }
  node_to_node_encryption { enabled = true }

  domain_endpoint_options {
    enforce_https       = true
    tls_security_policy = "Policy-Min-TLS-1-2-2019-07"
  }

  access_policies = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { AWS = "*" }
        Action    = "es:*"
        Resource  = "arn:aws:es:${var.aws_region}:${data.aws_caller_identity.current.account_id}:domain/${var.project_name}/*"
      }
    ]
  })

  tags = { Name = "${var.project_name}-opensearch" }
}

# rolesmapping은 user_data.sh에서 EC2 부팅 시 자동 실행됨
# (OTel Collector IAM role → OpenSearch all_access 매핑)

# ============================================================
# Glue Database + Crawler (S3 → Athena 장기 분석)
# ============================================================

resource "aws_glue_catalog_database" "observability" {
  name = "${replace(var.project_name, "-", "_")}_observability"
}

resource "aws_iam_role" "glue_crawler" {
  name = "${var.project_name}-glue-crawler-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "glue.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "glue_service" {
  role       = aws_iam_role.glue_crawler.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole"
}

resource "aws_iam_role_policy" "glue_s3" {
  name = "${var.project_name}-glue-s3-policy"
  role = aws_iam_role.glue_crawler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["s3:GetObject", "s3:ListBucket"]
      Resource = [
        aws_s3_bucket.logs_backup.arn,
        "${aws_s3_bucket.logs_backup.arn}/*",
        aws_s3_bucket.traces_backup.arn,
        "${aws_s3_bucket.traces_backup.arn}/*",
        aws_s3_bucket.metrics_backup.arn,
        "${aws_s3_bucket.metrics_backup.arn}/*",
      ]
    }]
  })
}

locals {
  log_columns_type   = "array<struct<resource:struct<attributes:array<struct<key:string,value:struct<stringvalue:string,intvalue:string,boolvalue:boolean,doublevalue:double>>>>,scopelogs:array<struct<logrecords:array<struct<timeunixnano:string,observedtimeunixnano:string,severitytext:string,severitynumber:int,body:struct<stringvalue:string>,attributes:array<struct<key:string,value:struct<stringvalue:string,intvalue:string>>>,traceid:string,spanid:string>>>>>>"
  time_projection    = {
    "projection.enabled"       = "true"
    "projection.year.type"     = "integer"
    "projection.year.range"    = "2024,2030"
    "projection.year.digits"   = "4"
    "projection.month.type"    = "integer"
    "projection.month.range"   = "1,12"
    "projection.month.digits"  = "2"
    "projection.day.type"      = "integer"
    "projection.day.range"     = "1,31"
    "projection.day.digits"    = "2"
    "projection.hour.type"     = "integer"
    "projection.hour.range"    = "0,23"
    "projection.hour.digits"   = "2"
    "projection.minute.type"   = "integer"
    "projection.minute.range"  = "0,59"
    "projection.minute.digits" = "2"
  }
  env_projection = {
    "projection.deployment_environment.type"   = "enum"
    "projection.deployment_environment.values" = "dev,prod"
  }
}

resource "aws_glue_catalog_table" "otel_logs_app" {
  name          = "otel_logs_app"
  database_name = aws_glue_catalog_database.observability.name
  table_type    = "EXTERNAL_TABLE"

  parameters = merge(local.time_projection, local.env_projection, {
    "classification"            = "json"
    "storage.location.template" = "s3://${aws_s3_bucket.logs_backup.id}/$${deployment_environment}/app/year=$${year}/month=$${month}/day=$${day}/hour=$${hour}/minute=$${minute}"
  })

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.logs_backup.id}/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"
    compressed    = true

    ser_de_info {
      serialization_library = "org.openx.data.jsonserde.JsonSerDe"
      parameters = {
        "ignore.malformed.json" = "TRUE"
        "dots.in.keys"          = "FALSE"
        "case.insensitive"      = "TRUE"
        "mapping"               = "TRUE"
      }
    }

    columns {
      name = "resourcelogs"
      type = local.log_columns_type
    }
  }

  partition_keys {
    name = "deployment_environment"
    type = "string"
  }
  partition_keys {
    name = "year"
    type = "string"
  }
  partition_keys {
    name = "month"
    type = "string"
  }
  partition_keys {
    name = "day"
    type = "string"
  }
  partition_keys {
    name = "hour"
    type = "string"
  }
  partition_keys {
    name = "minute"
    type = "string"
  }
}

resource "aws_glue_catalog_table" "otel_logs_host" {
  name          = "otel_logs_host"
  database_name = aws_glue_catalog_database.observability.name
  table_type    = "EXTERNAL_TABLE"

  parameters = merge(local.time_projection, local.env_projection, {
    "classification"            = "json"
    "storage.location.template" = "s3://${aws_s3_bucket.logs_backup.id}/$${deployment_environment}/host/year=$${year}/month=$${month}/day=$${day}/hour=$${hour}/minute=$${minute}"
  })

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.logs_backup.id}/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"
    compressed    = true

    ser_de_info {
      serialization_library = "org.openx.data.jsonserde.JsonSerDe"
      parameters = {
        "ignore.malformed.json" = "TRUE"
        "dots.in.keys"          = "FALSE"
        "case.insensitive"      = "TRUE"
        "mapping"               = "TRUE"
      }
    }

    columns {
      name = "resourcelogs"
      type = local.log_columns_type
    }
  }

  partition_keys {
    name = "deployment_environment"
    type = "string"
  }
  partition_keys {
    name = "year"
    type = "string"
  }
  partition_keys {
    name = "month"
    type = "string"
  }
  partition_keys {
    name = "day"
    type = "string"
  }
  partition_keys {
    name = "hour"
    type = "string"
  }
  partition_keys {
    name = "minute"
    type = "string"
  }
}

resource "aws_glue_catalog_table" "otel_traces" {
  name          = "otel_traces"
  database_name = aws_glue_catalog_database.observability.name
  table_type    = "EXTERNAL_TABLE"

  parameters = merge(local.time_projection, local.env_projection, {
    "classification"            = "json"
    "storage.location.template" = "s3://${aws_s3_bucket.traces_backup.id}/$${deployment_environment}/app/year=$${year}/month=$${month}/day=$${day}/hour=$${hour}/minute=$${minute}"
  })

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.traces_backup.id}/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"
    compressed    = true

    ser_de_info {
      serialization_library = "org.openx.data.jsonserde.JsonSerDe"
      parameters = {
        "ignore.malformed.json" = "TRUE"
        "dots.in.keys"          = "FALSE"
        "case.insensitive"      = "TRUE"
        "mapping"               = "TRUE"
      }
    }

    columns {
      name = "resourcespans"
      type = "array<struct<resource:struct<attributes:array<struct<key:string,value:struct<stringvalue:string,intvalue:string,boolvalue:boolean,doublevalue:double>>>>,scopespans:array<struct<spans:array<struct<traceid:string,spanid:string,parentspanid:string,name:string,kind:int,starttimeunixnano:string,endtimeunixnano:string,attributes:array<struct<key:string,value:struct<stringvalue:string,intvalue:string>>>,status:struct<code:int,message:string>>>>>>>"
    }
  }

  partition_keys {
    name = "deployment_environment"
    type = "string"
  }
  partition_keys {
    name = "year"
    type = "string"
  }
  partition_keys {
    name = "month"
    type = "string"
  }
  partition_keys {
    name = "day"
    type = "string"
  }
  partition_keys {
    name = "hour"
    type = "string"
  }
  partition_keys {
    name = "minute"
    type = "string"
  }
}

resource "aws_glue_catalog_table" "otel_metrics" {
  name          = "otel_metrics"
  database_name = aws_glue_catalog_database.observability.name
  table_type    = "EXTERNAL_TABLE"

  parameters = merge(local.time_projection, local.env_projection, {
    "classification"                       = "json"
    "projection.source.type"               = "enum"
    "projection.source.values"             = "app,container,host"
    "storage.location.template"            = "s3://${aws_s3_bucket.metrics_backup.id}/$${deployment_environment}/$${source}/year=$${year}/month=$${month}/day=$${day}/hour=$${hour}/minute=$${minute}"
  })

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.metrics_backup.id}/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"
    compressed    = true

    ser_de_info {
      serialization_library = "org.openx.data.jsonserde.JsonSerDe"
      parameters = {
        "ignore.malformed.json" = "TRUE"
        "dots.in.keys"          = "FALSE"
        "case.insensitive"      = "TRUE"
        "mapping"               = "TRUE"
      }
    }

    columns {
      name = "resourcemetrics"
      type = "array<struct<resource:struct<attributes:array<struct<key:string,value:struct<stringvalue:string,intvalue:string,boolvalue:boolean,doublevalue:double>>>>,scopemetrics:array<struct<metrics:array<struct<name:string,description:string,unit:string,sum:struct<datapoints:array<struct<attributes:array<struct<key:string,value:struct<stringvalue:string,intvalue:string>>>,startTimeUnixNano:string,timeUnixNano:string,asDouble:double,asInt:string>>,aggregationTemporality:int,isMonotonic:boolean>>,gauge:struct<datapoints:array<struct<attributes:array<struct<key:string,value:struct<stringvalue:string,intvalue:string>>>,timeUnixNano:string,asDouble:double,asInt:string>>>>>>>>>"
    }
  }

  partition_keys {
    name = "deployment_environment"
    type = "string"
  }
  partition_keys {
    name = "source"
    type = "string"
  }
  partition_keys {
    name = "year"
    type = "string"
  }
  partition_keys {
    name = "month"
    type = "string"
  }
  partition_keys {
    name = "day"
    type = "string"
  }
  partition_keys {
    name = "hour"
    type = "string"
  }
  partition_keys {
    name = "minute"
    type = "string"
  }
}

# ============================================================
# Athena Workgroup + S3 쿼리 결과 버킷
# ============================================================

resource "aws_s3_bucket" "athena_results" {
  bucket = "${var.project_name}-athena-results-${data.aws_caller_identity.current.account_id}"
  tags   = { Name = "${var.project_name}-athena-results" }
}

resource "aws_s3_bucket_lifecycle_configuration" "athena_results" {
  bucket = aws_s3_bucket.athena_results.id
  rule {
    id     = "expire-query-results"
    status = "Enabled"
    expiration { days = 7 }
  }
}

resource "aws_athena_workgroup" "observability" {
  name          = "${var.project_name}-observability"
  force_destroy = true

  configuration {
    result_configuration {
      output_location = "s3://${aws_s3_bucket.athena_results.id}/query-results/"
    }
  }

  tags = { Name = "${var.project_name}-athena-workgroup" }
}

# ============================================================
# Cognito - 고객 OTel Collector 인증
# 고객별 client_id/secret 발급 → Envoy JWT 검증으로 우리 EC2 접근 인증
# ============================================================

resource "aws_cognito_user_pool" "otel_auth" {
  name = "${var.project_name}-otel-auth"

  password_policy {
    minimum_length    = 16
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = true
  }

  tags = { Name = "${var.project_name}-otel-auth" }
}

# Cognito Domain (토큰 엔드포인트 URL용)
resource "aws_cognito_user_pool_domain" "otel_auth" {
  domain       = "${var.project_name}-otel-auth"
  user_pool_id = aws_cognito_user_pool.otel_auth.id
}

# Resource Server (API 스코프 정의)
resource "aws_cognito_resource_server" "otel" {
  identifier   = "https://${var.project_name}.otel"
  name         = "${var.project_name}-otel-resource-server"
  user_pool_id = aws_cognito_user_pool.otel_auth.id

  scope {
    scope_name        = "ingest"
    scope_description = "OTel 데이터 수집 권한"
  }
}

# 고객용 App Client (client_credentials 방식 - 서버간 통신)
resource "aws_cognito_user_pool_client" "otel_customer" {
  name         = "${var.project_name}-otel-customer-client"
  user_pool_id = aws_cognito_user_pool.otel_auth.id

  allowed_oauth_flows                  = ["client_credentials"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["${aws_cognito_resource_server.otel.identifier}/ingest"]
  generate_secret                      = true

  depends_on = [aws_cognito_resource_server.otel]
}

# ============================================================
# API Gateway - 비활성화 (Envoy JWT 프록시로 대체)
# ============================================================

# resource "aws_api_gateway_rest_api" "otel" {
#   name        = "${var.project_name}-otel-gateway"
#   description = "OTel 데이터 수집 API Gateway"
#   tags = { Name = "${var.project_name}-otel-gateway" }
# }
# resource "aws_api_gateway_resource" "v1" {
#   rest_api_id = aws_api_gateway_rest_api.otel.id
#   parent_id   = aws_api_gateway_rest_api.otel.root_resource_id
#   path_part   = "v1"
# }
# resource "aws_api_gateway_resource" "logs" {
#   rest_api_id = aws_api_gateway_rest_api.otel.id
#   parent_id   = aws_api_gateway_resource.v1.id
#   path_part   = "logs"
# }
# resource "aws_api_gateway_resource" "traces" {
#   rest_api_id = aws_api_gateway_rest_api.otel.id
#   parent_id   = aws_api_gateway_resource.v1.id
#   path_part   = "traces"
# }
# resource "aws_api_gateway_resource" "metrics" {
#   rest_api_id = aws_api_gateway_rest_api.otel.id
#   parent_id   = aws_api_gateway_resource.v1.id
#   path_part   = "metrics"
# }
# resource "aws_api_gateway_method" "logs_post" {
#   rest_api_id      = aws_api_gateway_rest_api.otel.id
#   resource_id      = aws_api_gateway_resource.logs.id
#   http_method      = "POST"
#   authorization    = "NONE"
#   api_key_required = true
# }
# resource "aws_api_gateway_method" "traces_post" {
#   rest_api_id      = aws_api_gateway_rest_api.otel.id
#   resource_id      = aws_api_gateway_resource.traces.id
#   http_method      = "POST"
#   authorization    = "NONE"
#   api_key_required = true
# }
# resource "aws_api_gateway_method" "metrics_post" {
#   rest_api_id      = aws_api_gateway_rest_api.otel.id
#   resource_id      = aws_api_gateway_resource.metrics.id
#   http_method      = "POST"
#   authorization    = "NONE"
#   api_key_required = true
# }
# resource "aws_api_gateway_integration" "logs" {
#   rest_api_id             = aws_api_gateway_rest_api.otel.id
#   resource_id             = aws_api_gateway_resource.logs.id
#   http_method             = aws_api_gateway_method.logs_post.http_method
#   type                    = "HTTP_PROXY"
#   integration_http_method = "POST"
#   uri                     = "http://${aws_eip.otel_collector.public_ip}:4318/v1/logs"
# }
# resource "aws_api_gateway_integration" "traces" {
#   rest_api_id             = aws_api_gateway_rest_api.otel.id
#   resource_id             = aws_api_gateway_resource.traces.id
#   http_method             = aws_api_gateway_method.traces_post.http_method
#   type                    = "HTTP_PROXY"
#   integration_http_method = "POST"
#   uri                     = "http://${aws_eip.otel_collector.public_ip}:4318/v1/traces"
# }
# resource "aws_api_gateway_integration" "metrics" {
#   rest_api_id             = aws_api_gateway_rest_api.otel.id
#   resource_id             = aws_api_gateway_resource.metrics.id
#   http_method             = aws_api_gateway_method.metrics_post.http_method
#   type                    = "HTTP_PROXY"
#   integration_http_method = "POST"
#   uri                     = "http://${aws_eip.otel_collector.public_ip}:4318/v1/metrics"
# }
# resource "aws_api_gateway_deployment" "otel" {
#   rest_api_id = aws_api_gateway_rest_api.otel.id
#   depends_on = [
#     aws_api_gateway_integration.logs,
#     aws_api_gateway_integration.traces,
#     aws_api_gateway_integration.metrics,
#   ]
#   lifecycle { create_before_destroy = true }
# }
# resource "aws_api_gateway_stage" "otel" {
#   deployment_id = aws_api_gateway_deployment.otel.id
#   rest_api_id   = aws_api_gateway_rest_api.otel.id
#   stage_name    = "prod"
#   tags = { Name = "${var.project_name}-otel-stage" }
# }
# resource "aws_api_gateway_usage_plan" "otel" {
#   name = "${var.project_name}-otel-usage-plan"
#   api_stages {
#     api_id = aws_api_gateway_rest_api.otel.id
#     stage  = aws_api_gateway_stage.otel.stage_name
#   }
# }
# resource "aws_api_gateway_api_key" "customer_1" {
#   name    = "${var.project_name}-customer-1"
#   enabled = true
# }
# resource "aws_api_gateway_usage_plan_key" "customer_1" {
#   key_id        = aws_api_gateway_api_key.customer_1.id
#   key_type      = "API_KEY"
#   usage_plan_id = aws_api_gateway_usage_plan.otel.id
# }
