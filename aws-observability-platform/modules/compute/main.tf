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
          "${var.s3_logs_backup_arn}/*",
          "${var.s3_traces_backup_arn}/*",
          "${var.s3_metrics_backup_arn}/*"
        ]
      },
      {
        Sid    = "S3AthenaAccess"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:ListBucket", "s3:PutObject", "s3:GetBucketLocation"]
        Resource = [
          var.s3_athena_results_arn,
          "${var.s3_athena_results_arn}/*",
          var.s3_logs_backup_arn,
          "${var.s3_logs_backup_arn}/*",
          var.s3_traces_backup_arn,
          "${var.s3_traces_backup_arn}/*"
        ]
      },
      {
        Sid    = "S3DeployAccess"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
        Resource = [
          var.s3_deploy_arn,
          "${var.s3_deploy_arn}/*"
        ]
      },
      {
        Sid    = "OpenSearchAccess"
        Effect = "Allow"
        Action = ["es:ESHttp*"]
        Resource = [
          var.opensearch_arn,
          "${var.opensearch_arn}/*"
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
        Action = [
          "dynamodb:Scan", "dynamodb:GetItem", "dynamodb:Query",
          "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem",
          "dynamodb:BatchWriteItem",
        ]
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
      },
      {
        Sid    = "AgentCoreMemoryAccess"
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:CreateEvent",
          "bedrock-agentcore:RetrieveMemoryRecords",
          "bedrock-agentcore:ListMemoryRecords",
          "bedrock-agentcore:GetMemoryRecord",
          "bedrock-agentcore:DeleteMemoryRecord",
        ]
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
# EC2 - OTel Collector
# ============================================================

resource "aws_instance" "otel_collector" {
  ami                         = var.ec2_ami_id
  instance_type               = "t2.micro"
  subnet_id                   = var.public_subnet_id
  vpc_security_group_ids      = [var.otel_collector_sg_id]
  iam_instance_profile        = aws_iam_instance_profile.otel_collector.name
  key_name                    = var.ec2_key_name
  user_data_replace_on_change = true

  root_block_device {
    volume_size = 8
    volume_type = "gp3"
  }

  user_data_base64 = base64gzip(templatefile("${path.root}/user_data.sh", {
    amp_remote_write_url       = var.amp_endpoint
    amp_workspace_id           = var.amp_workspace_id
    aws_region                 = var.aws_region
    s3_logs_bucket             = var.s3_logs_backup_id
    s3_traces_bucket           = var.s3_traces_backup_id
    s3_metrics_bucket          = var.s3_metrics_backup_id
    project_name               = var.project_name
    project_name_underscore    = replace(var.project_name, "-", "_")
    cognito_user_pool_id       = var.cognito_user_pool_id
    cognito_client_id          = var.cognito_client_id
    opensearch_endpoint        = var.opensearch_endpoint
    opensearch_master_user     = var.opensearch_master_user
    opensearch_master_password = var.opensearch_master_password
    otel_collector_role_arn    = aws_iam_role.otel_collector.arn
    agentcore_memory_id        = var.agentcore_memory_id
    athena_results_bucket      = var.s3_athena_results_id
    logs_bucket                = var.s3_logs_backup_id
    traces_bucket              = var.s3_traces_backup_id
    deploy_bucket              = var.s3_deploy_id
    chatbot_conversations_table = var.chatbot_conversations_table
    chatbot_messages_table      = var.chatbot_messages_table
  }))

  tags = { Name = "${var.project_name}-otel-collector" }

}

resource "aws_eip" "otel_collector" {
  instance = aws_instance.otel_collector.id
  domain   = "vpc"
  tags     = { Name = "${var.project_name}-otel-collector-eip" }
}
