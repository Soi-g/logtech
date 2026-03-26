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

resource "aws_sns_topic" "alerts" {
  name = "${var.project_name}-alerts"
  tags = { Name = "${var.project_name}-alerts" }
}

resource "aws_sns_topic_policy" "alerts" {
  arn = aws_sns_topic.alerts.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "aps.amazonaws.com" }
        Action    = "sns:Publish"
        Resource  = aws_sns_topic.alerts.arn
      }
    ]
  })
}

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
      AWS_REGION_NAME         = var.aws_region
      AGENTCORE_MEMORY_ID     = var.agentcore_memory_id
      AGENTCORE_RUNTIME_ARN   = var.agentcore_runtime_arn
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
  source_arn    = aws_sns_topic.alerts.arn
}

resource "aws_sns_topic_subscription" "lambda" {
  topic_arn = aws_sns_topic.alerts.arn
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
# AMP Alert Rules & Alertmanager
# ============================================================

resource "aws_prometheus_rule_group_namespace" "alerts" {
  name         = "observability-alerts"
  workspace_id = var.amp_workspace_id

  data = <<-YAML
    groups:

      # ============================================================
      # 서비스 레지스트리 (Recording Rule)
      # ============================================================
      - name: service-registry
        interval: 1m
        rules:
          - record: job:http_known_services:presence
            expr: |
              (
                group by (job, deployment_environment)(
                  last_over_time(http_server_request_duration_seconds_count[24h])
                )
              ) or (
                group by (job, deployment_environment)(
                  last_over_time(http_server_duration_milliseconds_count[24h])
                )
              )

      # ============================================================
      # HTTP 에러율 파생 메트릭
      # ============================================================
      - name: http-error-rates
        interval: 1m
        rules:
          - record: job:http_4xx_error_ratio:rate5m
            expr: |
              (
                sum by (job, deployment_environment, source)(
                  rate(http_server_request_duration_seconds_count{http_response_status_code=~"4.."}[5m])
                )
                or
                sum by (job, deployment_environment, source)(
                  rate(http_server_request_duration_seconds_count[5m]) * 0
                )
              )
              /
              sum by (job, deployment_environment, source)(
                rate(http_server_request_duration_seconds_count[5m])
              )

          - record: job:http_5xx_error_ratio:rate5m
            expr: |
              (
                sum by (job, deployment_environment, source)(
                  rate(http_server_request_duration_seconds_count{http_response_status_code=~"5.."}[5m])
                )
                or
                sum by (job, deployment_environment, source)(
                  rate(http_server_request_duration_seconds_count[5m]) * 0
                )
              )
              /
              sum by (job, deployment_environment, source)(
                rate(http_server_request_duration_seconds_count[5m])
              )

      # ============================================================
      # 범용 알람
      # ============================================================
      - name: universal-alerts
        interval: 1m
        rules:

          - alert: ServiceDown
            expr: |
              job:http_known_services:presence
              unless
              (
                group by (job, deployment_environment)(
                  rate(http_server_request_duration_seconds_count[5m])
                )
                or
                group by (job, deployment_environment)(
                  rate(http_server_duration_milliseconds_count[5m])
                )
              )
            for: 5m
            labels:
              severity: critical
            annotations:
              summary: "서비스 메트릭 수신 중단 - {{ $labels.job }}"
              description: "{{ $labels.job }} ({{ $labels.deployment_environment }}) 서비스의 HTTP 메트릭이 5분 이상 수신되지 않습니다. 서비스가 다운됐거나 OTel 수집이 중단됐을 수 있습니다"

          - alert: Http4xxErrorRate
            expr: job:http_4xx_error_ratio:rate5m > 0.05
            for: 2m
            labels:
              severity: warning
            annotations:
              summary: "HTTP 4xx 에러율 5% 초과 - {{ $labels.job }}"
              description: "{{ $labels.job }} ({{ $labels.deployment_environment }}) 서비스의 4xx 에러율이 {{ $value | humanizePercentage }} 입니다. 잘못된 요청 경로 또는 클라이언트 에러 가능성"

          - alert: Unexpected4xxDetected
            expr: |
              job:http_4xx_error_ratio:rate5m > 0.01
              and
              avg_over_time(job:http_4xx_error_ratio:rate5m[30m] offset 5m) < 0.005
            for: 2m
            labels:
              severity: warning
            annotations:
              summary: "비정상 4xx 급등 (평소 없던 에러) - {{ $labels.job }}"
              description: "{{ $labels.job }} ({{ $labels.deployment_environment }}) 서비스에 평소 없던 4xx 에러 발생 중 (현재: {{ $value | humanizePercentage }}). 잘못된 경로 요청 또는 클라이언트 변경 가능성"

          - alert: Http5xxErrorRate
            expr: job:http_5xx_error_ratio:rate5m > 0.01
            for: 2m
            labels:
              severity: critical
            annotations:
              summary: "HTTP 5xx 에러율 1% 초과 - {{ $labels.job }}"
              description: "{{ $labels.job }} ({{ $labels.deployment_environment }}) 서비스의 5xx 에러율이 {{ $value | humanizePercentage }} 입니다. 서버 내부 오류 가능성"
  YAML
}

resource "aws_prometheus_alert_manager_definition" "main" {
  workspace_id = var.amp_workspace_id

  definition = <<-YAML
    alertmanager_config: |
      global:
        resolve_timeout: 5m

      route:
        group_by: ['alertname', 'job', 'deployment_environment']
        group_wait: 30s
        group_interval: 5m
        repeat_interval: 4h
        receiver: sns-alert

        routes:
          - match:
              severity: critical
            receiver: sns-alert
            group_wait: 30s
            group_interval: 5m
            repeat_interval: 1h

          - match:
              severity: warning
            receiver: sns-alert
            group_wait: 1m
            group_interval: 10m
            repeat_interval: 12h

      inhibit_rules:
        - source_match:
            severity: critical
          target_match:
            severity: warning
          equal:
            - job
            - deployment_environment

        - source_match:
            alertname: Http4xxErrorRate
          target_match:
            alertname: Unexpected4xxDetected
          equal:
            - job
            - deployment_environment

      receivers:
        - name: sns-alert
          sns_configs:
            - topic_arn: ${aws_sns_topic.alerts.arn}
              sigv4:
                region: ${var.aws_region}
              attributes:
                severity: '{{ .CommonLabels.severity }}'
  YAML
}
