# ============================================================
# Lambda Security Group
# ============================================================

resource "aws_security_group" "lambda" {
  name        = "${var.project_name}-lambda-sg"
  description = "Lambda - private subnet, outbound via NAT Gateway"
  vpc_id      = var.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project_name}-lambda-sg" }
}

resource "aws_security_group_rule" "opensearch_from_lambda" {
  type                     = "ingress"
  from_port                = 443
  to_port                  = 443
  protocol                 = "tcp"
  security_group_id        = var.opensearch_sg_id
  source_security_group_id = aws_security_group.lambda.id
  description              = "Allow Lambda to access OpenSearch"
}

# ============================================================
# SNS Topic
# ============================================================


# ============================================================
# Lambda IAM Role
# ============================================================

resource "aws_iam_role" "lambda_agent" {
  name = "${var.project_name}-lambda-agent-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "lambda_agent" {
  name = "${var.project_name}-lambda-agent-policy"
  role = aws_iam_role.lambda_agent.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Sid      = "AMP"
        Effect   = "Allow"
        Action   = ["aps:QueryMetrics", "aps:GetSeries", "aps:GetLabels", "aps:GetMetricMetadata", "aps:RemoteRead"]
        Resource = "*"
      },
      {
        Sid      = "OpenSearch"
        Effect   = "Allow"
        Action   = ["es:ESHttpGet", "es:ESHttpPost"]
        Resource = "${var.opensearch_arn}/*"
      },
      {
        Sid      = "Bedrock"
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = "*"
      },
      {
        Sid      = "S3"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${var.s3_deploy_arn}/lambda/*"
      },
      {
        Sid      = "SelfInvoke"
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = "arn:aws:lambda:${var.aws_region}:*:function:${var.project_name}-observability-agent"
      },
      {
        Sid    = "VPC"
        Effect = "Allow"
        Action = [
          "ec2:CreateNetworkInterface",
          "ec2:DescribeNetworkInterfaces",
          "ec2:DeleteNetworkInterface",
          "ec2:AssignPrivateIpAddresses",
          "ec2:UnassignPrivateIpAddresses"
        ]
        Resource = "*"
      },
      {
        Sid    = "EC2Read"
        Effect = "Allow"
        Action = [
          "ec2:DescribeInstances",
          "ec2:DescribeInstanceStatus",
          "ec2:DescribeSecurityGroups"
        ]
        Resource = "*"
      },
      {
        Sid    = "RDSRead"
        Effect = "Allow"
        Action = [
          "rds:DescribeDBInstances",
          "rds:DescribeEvents"
        ]
        Resource = "*"
      },
      {
        Sid    = "CloudWatchRead"
        Effect = "Allow"
        Action = [
          "cloudwatch:GetMetricStatistics",
          "cloudwatch:DescribeAlarms"
        ]
        Resource = "*"
      },
      {
        Sid    = "CloudWatchLogsRead"
        Effect = "Allow"
        Action = [
          "logs:FilterLogEvents",
          "logs:DescribeLogStreams",
          "logs:GetLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Sid    = "CloudTrailRead"
        Effect = "Allow"
        Action = ["cloudtrail:LookupEvents"]
        Resource = "*"
      },
      {
        Sid    = "ELBRead"
        Effect = "Allow"
        Action = [
          "elasticloadbalancing:DescribeLoadBalancers",
          "elasticloadbalancing:DescribeTargetGroups",
          "elasticloadbalancing:DescribeTargetHealth"
        ]
        Resource = "*"
      },
      {
        Sid    = "AutoScalingRead"
        Effect = "Allow"
        Action = [
          "autoscaling:DescribeAutoScalingGroups",
          "autoscaling:DescribeScalingActivities"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "time_sleep" "lambda_iam_propagation" {
  depends_on      = [aws_iam_role_policy.lambda_agent]
  create_duration = "15s"
}

# ============================================================
# Lambda 패키징 & 배포
# ============================================================

data "archive_file" "lambda_agent" {
  type        = "zip"
  source_dir  = "${path.root}/lambda_package"
  output_path = "${path.root}/lambda_handler.zip"
}

resource "aws_s3_object" "lambda_agent" {
  bucket = var.s3_deploy_id
  key    = "lambda/lambda_handler.zip"
  source = data.archive_file.lambda_agent.output_path
  etag   = data.archive_file.lambda_agent.output_md5
}

resource "aws_s3_object" "agent_deps_layer" {
  bucket = var.s3_deploy_id
  key    = "lambda/lambda_layer.zip"
  source = "${path.root}/lambda_layer.zip"
  etag   = filemd5("${path.root}/lambda_layer.zip")
}

resource "aws_lambda_layer_version" "agent_deps" {
  layer_name          = "${var.project_name}-agent-deps"
  s3_bucket           = var.s3_deploy_id
  s3_key              = aws_s3_object.agent_deps_layer.key
  source_code_hash    = filebase64sha256("${path.root}/lambda_layer.zip")
  compatible_runtimes = ["python3.12"]
}

# ============================================================
# Lambda 함수
# ============================================================

resource "aws_lambda_function" "agent" {
  function_name    = "${var.project_name}-observability-agent"
  role             = aws_iam_role.lambda_agent.arn
  handler          = "bedrock_agent_runtime_handler.lambda_handler"
  runtime          = "python3.12"
  s3_bucket        = var.s3_deploy_id
  s3_key           = aws_s3_object.lambda_agent.key
  source_code_hash = data.archive_file.lambda_agent.output_base64sha256
  layers           = [aws_lambda_layer_version.agent_deps.arn]
  timeout          = 900
  memory_size      = 512

  vpc_config {
    subnet_ids         = [var.private_subnet_id]
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = {
      BEDROCK_AGENT_ID        = var.bedrock_agent_id
      BEDROCK_AGENT_ALIAS_ID  = var.bedrock_agent_alias_id
      DYNAMODB_INCIDENT_TABLE = var.dynamodb_incident_table
      AMP_ENDPOINT            = "${var.amp_endpoint}api/v1/"
      OPENSEARCH_ENDPOINT     = var.opensearch_endpoint
      OPENSEARCH_USER         = var.opensearch_master_user
      OPENSEARCH_PASSWORD     = var.opensearch_master_password
      SLACK_BOT_TOKEN         = var.slack_bot_token
      SLACK_CHANNEL           = var.slack_channel
      SLACK_SIGNING_SECRET    = var.slack_signing_secret
      AWS_REGION_NAME         = var.aws_region
      AGENTCORE_MEMORY_ID     = var.agentcore_memory_id
      AGENTCORE_RUNTIME_ARN   = var.agentcore_runtime_arn
      # LangSmith — LangGraph 실행 트레이싱 (노드별 입출력, 툴 호출, 소요 시간 기록)
      LANGCHAIN_TRACING_V2    = "true"
      LANGCHAIN_API_KEY       = var.langsmith_api_key
      LANGCHAIN_PROJECT       = "observability-rca"
    }
  }

  tags = { Name = "${var.project_name}-observability-agent" }

  depends_on = [
    aws_iam_role_policy.lambda_agent,
    time_sleep.lambda_iam_propagation
  ]
}

resource "aws_lambda_permission" "sns_trigger" {
  statement_id  = "AllowSNSTrigger"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.agent.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = var.sns_topic_arn
}

resource "aws_sns_topic_subscription" "lambda" {
  topic_arn = var.sns_topic_arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.agent.arn
}

resource "aws_lambda_function_url" "agent" {
  function_name      = aws_lambda_function.agent.function_name
  authorization_type = "NONE"
}

resource "aws_lambda_permission" "agent_url" {
  statement_id           = "AllowFunctionURLInvoke"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.agent.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

# ============================================================
