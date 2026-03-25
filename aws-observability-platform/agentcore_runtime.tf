# ============================================================
# AgentCore Runtime - LangGraph를 컨테이너에서 실행
# Lambda timeout 없이 최대 8시간 실행 가능
# ============================================================

# ECR Repository
resource "aws_ecr_repository" "agentcore_runtime" {
  name                 = "${var.project_name}-agentcore-runtime"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = false
  }

  tags = {
    Name        = "${var.project_name}-agentcore-runtime"
    ManagedBy   = "Terraform"
  }
}

# ECR lifecycle policy - 최근 3개 이미지만 유지
resource "aws_ecr_lifecycle_policy" "agentcore_runtime" {
  repository = aws_ecr_repository.agentcore_runtime.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 3 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 3
      }
      action = { type = "expire" }
    }]
  })
}

# IAM Role - AgentCore Runtime이 AWS 서비스 접근
resource "aws_iam_role" "agentcore_runtime" {
  name = "${var.project_name}-agentcore-runtime-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock-agentcore.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = { ManagedBy = "Terraform" }
}

resource "aws_iam_role_policy" "agentcore_runtime" {
  name = "${var.project_name}-agentcore-runtime-policy"
  role = aws_iam_role.agentcore_runtime.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "BedrockAccess"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream"
        ]
        Resource = "*"
      },
      {
        Sid    = "AgentCoreMemoryAccess"
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:BatchCreateMemoryRecords",
          "bedrock-agentcore:RetrieveMemoryRecords",
          "bedrock-agentcore:BatchDeleteMemoryRecords"
        ]
        Resource = "*"
      },
      {
        Sid    = "DynamoDBAccess"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:Query",
          "dynamodb:Scan"
        ]
        Resource = "*"
      },
      {
        Sid    = "AmpAccess"
        Effect = "Allow"
        Action = [
          "aps:QueryMetrics",
          "aps:GetLabels",
          "aps:GetSeries",
          "aps:GetMetricMetadata"
        ]
        Resource = "*"
      },
      {
        Sid    = "OpenSearchAccess"
        Effect = "Allow"
        Action = ["es:ESHttpGet", "es:ESHttpPost"]
        Resource = "*"
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "*"
      },
      {
        Sid    = "MarketplaceAccess"
        Effect = "Allow"
        Action = [
          "aws-marketplace:ViewSubscriptions",
          "aws-marketplace:Subscribe"
        ]
        Resource = "*"
      },
      {
        Sid    = "ECRAccess"
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer"
        ]
        Resource = "*"
      },
      {
        Sid    = "InfrastructureRead"
        Effect = "Allow"
        Action = [
          "rds:DescribeDBInstances",
          "rds:DescribeDBClusters",
          "rds:DescribeEvents",
          "ec2:DescribeInstances",
          "ec2:DescribeInstanceStatus",
          "ecs:DescribeServices",
          "ecs:DescribeTasks",
          "cloudwatch:GetMetricData",
          "cloudwatch:GetMetricStatistics",
          "cloudtrail:LookupEvents"
        ]
        Resource = "*"
      }
    ]
  })
}

# Lambda에 AgentCore Runtime invoke 권한 추가
resource "aws_iam_role_policy" "lambda_agentcore_invoke" {
  name = "${var.project_name}-lambda-agentcore-invoke"
  role = aws_iam_role.lambda_agent.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "AgentCoreRuntimeInvoke"
      Effect = "Allow"
      Action = [
        "bedrock-agentcore:InvokeAgentRuntime",
        "bedrock-agentcore:InvokeAgentRuntimeForUser"
      ]
      Resource = "*"
    }]
  })
}

# ECR ARN output (AgentCore Runtime 생성 스크립트에서 사용)
output "ecr_repository_url" {
  value       = aws_ecr_repository.agentcore_runtime.repository_url
  description = "ECR repository URL for AgentCore Runtime"
}

output "agentcore_runtime_role_arn" {
  value       = aws_iam_role.agentcore_runtime.arn
  description = "IAM role ARN for AgentCore Runtime"
}
