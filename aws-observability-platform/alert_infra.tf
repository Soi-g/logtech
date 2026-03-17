# ============================================================
# Lambda용 Security Group
# ============================================================

resource "aws_security_group" "lambda" {
  name        = "${var.project_name}-lambda-sg"
  description = "Lambda - private subnet, outbound via NAT Gateway"
  vpc_id      = aws_vpc.main.id

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
  security_group_id        = aws_security_group.opensearch.id
  source_security_group_id = aws_security_group.lambda.id
  description              = "Allow Lambda to access OpenSearch"
}

# ============================================================
# SNS Topic - Alertmanager가 알람을 쏘는 곳
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
        Resource = "${aws_opensearch_domain.main.arn}/*"
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
        Resource = "${aws_s3_bucket.runbooks.arn}/lambda/*"
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
        Action = [
          "cloudtrail:LookupEvents"
        ]
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

# ============================================================
# Lambda 함수
# ============================================================

data "archive_file" "lambda_agent" {
  type        = "zip"
  source_dir  = "${path.module}/lambda_package"
  output_path = "${path.module}/lambda_handler.zip"
}

# S3에 코드 zip 업로드
resource "aws_s3_object" "lambda_agent" {
  bucket = aws_s3_bucket.runbooks.id
  key    = "lambda/lambda_handler.zip"
  source = data.archive_file.lambda_agent.output_path
  etag   = data.archive_file.lambda_agent.output_md5
}

# S3에 layer zip 업로드
resource "aws_s3_object" "agent_deps_layer" {
  bucket = aws_s3_bucket.runbooks.id
  key    = "lambda/lambda_layer.zip"
  source = "${path.module}/lambda_layer.zip"
  etag   = filemd5("${path.module}/lambda_layer.zip")
}

# Lambda Layer - S3에서 로드
resource "aws_lambda_layer_version" "agent_deps" {
  layer_name          = "${var.project_name}-agent-deps"
  s3_bucket           = aws_s3_bucket.runbooks.id
  s3_key              = aws_s3_object.agent_deps_layer.key
  source_code_hash    = filebase64sha256("${path.module}/lambda_layer.zip")
  compatible_runtimes = ["python3.12"]
}

resource "aws_lambda_function" "agent" {
  function_name    = "${var.project_name}-observability-agent"
  role             = aws_iam_role.lambda_agent.arn
  handler          = "bedrock_agent_runtime_handler.lambda_handler"
  runtime          = "python3.12"
  s3_bucket        = aws_s3_bucket.runbooks.id
  s3_key           = aws_s3_object.lambda_agent.key
  source_code_hash = data.archive_file.lambda_agent.output_base64sha256
  layers           = [aws_lambda_layer_version.agent_deps.arn]
  timeout          = 300
  memory_size      = 512

  vpc_config {
    subnet_ids         = [aws_subnet.private.id]
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = {
      BEDROCK_AGENT_ID        = aws_bedrockagent_agent.observability.id
      BEDROCK_AGENT_ALIAS_ID  = aws_bedrockagent_agent_alias.prod.agent_alias_id
      DYNAMODB_INCIDENT_TABLE = aws_dynamodb_table.incident_ongoing.name
      AMP_ENDPOINT            = "${aws_prometheus_workspace.main.prometheus_endpoint}api/v1/"
      OPENSEARCH_ENDPOINT     = aws_opensearch_domain.main.endpoint
      OPENSEARCH_USER         = var.opensearch_master_user
      OPENSEARCH_PASSWORD     = var.opensearch_master_password
      BEDROCK_KB_ID           = aws_bedrockagent_knowledge_base.runbooks.id
      SLACK_BOT_TOKEN         = var.slack_bot_token
      SLACK_CHANNEL           = var.slack_channel
      AWS_REGION_NAME         = var.aws_region
      AGENTCORE_MEMORY_ID     = var.agentcore_memory_id
    }
  }

  tags = { Name = "${var.project_name}-observability-agent" }

  depends_on = [
    aws_bedrockagent_agent.observability,
    aws_bedrockagent_agent_alias.prod,
    aws_iam_role_policy.lambda_agent
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

output "agent_function_url" {
  value = aws_lambda_function_url.agent.function_url
}

# ============================================================
# AMP Alert Rule
# ============================================================

resource "aws_prometheus_rule_group_namespace" "alerts" {
  name         = "observability-alerts"
  workspace_id = aws_prometheus_workspace.main.id

  data = <<-YAML
    groups:

      # ============================================================
      # 서비스 레지스트리 (Recording Rule)
      # 최근 24h 내 메트릭을 보낸 서비스 목록 유지
      # 새 서비스는 자동 등록, 하드코딩 불필요
      # ============================================================
      - name: service-registry
        interval: 1m
        rules:
          - record: job:http_known_services:presence
            expr: |
              group by (job, deployment_environment)(
                last_over_time(http_server_request_duration_seconds_count[24h])
              )

      # ============================================================
      # 범용 레이어 - OTel 표준 메트릭 기반 (언어/프레임워크 무관)
      # ============================================================
      - name: universal-alerts
        interval: 1m
        rules:

          # ── 서비스 다운 감지 ──────────────────────────────────
          # 레지스트리에 있는 서비스인데 현재 rate가 없으면 발동
          # per-service 감지, 하드코딩 없이 모든 서비스에 범용 동작
          - alert: ServiceDown
            expr: |
              job:http_known_services:presence
              unless
              group by (job, deployment_environment)(
                rate(http_server_request_duration_seconds_count[5m])
              )
            for: 5m
            labels:
              severity: critical
            annotations:
              summary: "서비스 메트릭 수신 중단 - {{ $labels.job }}"
              description: "{{ $labels.job }} ({{ $labels.deployment_environment }}) 서비스의 HTTP 메트릭이 5분 이상 수신되지 않습니다. 서비스가 다운됐거나 OTel 수집이 중단됐을 수 있습니다"

          # ── HTTP 5xx 에러율 ───────────────────────────────────
          - alert: HighHttpErrorRate
            expr: |
              sum by (service_name, service_namespace)(
                rate(http_server_request_duration_seconds_count{http_response_status_code=~"5.."}[5m])
              )
              /
              sum by (service_name, service_namespace)(
                rate(http_server_request_duration_seconds_count[5m])
              ) > 0.01
            for: 5m
            labels:
              severity: warning
            annotations:
              summary: "HTTP 5xx 에러율 1% 초과"
              description: "서비스 {{ $labels.service_name }} ({{ $labels.service_namespace }})의 5xx 에러율이 {{ $value | humanizePercentage }} 입니다"

          - alert: CriticalHttpErrorRate
            expr: |
              sum by (service_name, service_namespace)(
                rate(http_server_request_duration_seconds_count{http_response_status_code=~"5.."}[5m])
              )
              /
              sum by (service_name, service_namespace)(
                rate(http_server_request_duration_seconds_count[5m])
              ) > 0.05
            for: 2m
            labels:
              severity: critical
            annotations:
              summary: "HTTP 5xx 에러율 5% 초과"
              description: "서비스 {{ $labels.service_name }} ({{ $labels.service_namespace }})의 5xx 에러율이 {{ $value | humanizePercentage }} 입니다"

          # ── HTTP 응답시간 ─────────────────────────────────────
          - alert: HighHttpLatencyP95
            expr: |
              histogram_quantile(0.95,
                sum by (service_name, service_namespace, le)(
                  rate(http_server_request_duration_seconds_bucket[5m])
                )
              ) > 1.0
            for: 5m
            labels:
              severity: warning
            annotations:
              summary: "HTTP P95 응답시간 1초 초과"
              description: "서비스 {{ $labels.service_name }} ({{ $labels.service_namespace }})의 P95 응답시간이 {{ $value }}초 입니다"

          - alert: CriticalHttpLatencyP99
            expr: |
              histogram_quantile(0.99,
                sum by (service_name, service_namespace, le)(
                  rate(http_server_request_duration_seconds_bucket[5m])
                )
              ) > 3.0
            for: 2m
            labels:
              severity: critical
            annotations:
              summary: "HTTP P99 응답시간 3초 초과"
              description: "서비스 {{ $labels.service_name }} ({{ $labels.service_namespace }})의 P99 응답시간이 {{ $value }}초 입니다"

          # ── DB 커넥션 (OTel 표준 - 언어 무관) ───────────────
          - alert: HighDbConnectionPending
            expr: |
              sum by (service_name, service_namespace)(
                db_client_connections_pending_requests
              ) > 5
            for: 2m
            labels:
              severity: warning
            annotations:
              summary: "DB 커넥션 대기 급증"
              description: "서비스 {{ $labels.service_name }} ({{ $labels.service_namespace }})의 DB 커넥션 대기가 {{ $value }}개 입니다"

          - alert: CriticalDbConnectionPending
            expr: |
              sum by (service_name, service_namespace)(
                db_client_connections_pending_requests
              ) > 10
            for: 1m
            labels:
              severity: critical
            annotations:
              summary: "DB 커넥션 대기 심각 (10개 초과)"
              description: "서비스 {{ $labels.service_name }} ({{ $labels.service_namespace }})의 DB 커넥션 대기가 {{ $value }}개 입니다"

      # ============================================================
      # JVM 런타임 레이어 - Java 앱에서만 자동 발동
      # (메트릭 없는 앱은 자동으로 스킵됨)
      # ============================================================
      - name: jvm-alerts
        interval: 1m
        rules:

          # ── CPU ──────────────────────────────────────────────
          - alert: HighJvmCpu
            expr: |
              sum by (service_name, service_namespace)(
                jvm_cpu_recent_utilization_ratio
              ) > 0.8
            for: 5m
            labels:
              severity: warning
            annotations:
              summary: "JVM CPU 사용률 80% 초과"
              description: "서비스 {{ $labels.service_name }} ({{ $labels.service_namespace }})의 CPU가 {{ $value | humanizePercentage }} 입니다"

          - alert: CriticalJvmCpu
            expr: |
              sum by (service_name, service_namespace)(
                jvm_cpu_recent_utilization_ratio
              ) > 0.9
            for: 2m
            labels:
              severity: critical
            annotations:
              summary: "JVM CPU 사용률 90% 초과"
              description: "서비스 {{ $labels.service_name }} ({{ $labels.service_namespace }})의 CPU가 {{ $value | humanizePercentage }} 입니다"

          # ── Heap Memory ──────────────────────────────────────
          - alert: HighJvmHeapMemory
            expr: |
              sum by (service_name, service_namespace)(
                jvm_memory_used_bytes{jvm_memory_type="heap"}
              )
              /
              sum by (service_name, service_namespace)(
                jvm_memory_limit_bytes{jvm_memory_type="heap"}
              ) > 0.8
            for: 5m
            labels:
              severity: warning
            annotations:
              summary: "JVM Heap 메모리 80% 초과"
              description: "서비스 {{ $labels.service_name }} ({{ $labels.service_namespace }})의 Heap 사용률이 {{ $value | humanizePercentage }} 입니다"

          - alert: CriticalJvmHeapMemory
            expr: |
              sum by (service_name, service_namespace)(
                jvm_memory_used_bytes{jvm_memory_type="heap"}
              )
              /
              sum by (service_name, service_namespace)(
                jvm_memory_limit_bytes{jvm_memory_type="heap"}
              ) > 0.9
            for: 2m
            labels:
              severity: critical
            annotations:
              summary: "JVM Heap 메모리 90% 초과 - OOM 위험"
              description: "서비스 {{ $labels.service_name }} ({{ $labels.service_namespace }})의 Heap 사용률이 {{ $value | humanizePercentage }} 입니다"

          # ── GC ───────────────────────────────────────────────
          - alert: HighJvmGcTime
            expr: |
              sum by (service_name, service_namespace)(
                rate(jvm_gc_duration_seconds_sum[5m])
              ) > 0.05
            for: 5m
            labels:
              severity: warning
            annotations:
              summary: "JVM GC 시간 과다 (벽시계 시간의 5% 초과)"
              description: "서비스 {{ $labels.service_name }} ({{ $labels.service_namespace }})의 GC 소요 시간이 높습니다"

          # ── Threads ──────────────────────────────────────────
          - alert: JvmThreadDeadlock
            expr: |
              sum by (service_name, service_namespace)(
                jvm_thread_count{jvm_thread_state="deadlocked"}
              ) > 0
            for: 1m
            labels:
              severity: critical
            annotations:
              summary: "JVM 데드락 스레드 감지"
              description: "서비스 {{ $labels.service_name }} ({{ $labels.service_namespace }})에서 데드락이 발생했습니다"

          - alert: HighJvmBlockedThreads
            expr: |
              sum by (service_name, service_namespace)(
                jvm_thread_count{jvm_thread_state="blocked"}
              ) > 50
            for: 5m
            labels:
              severity: warning
            annotations:
              summary: "JVM BLOCKED 스레드 과다 (50개 초과)"
              description: "서비스 {{ $labels.service_name }} ({{ $labels.service_namespace }})의 BLOCKED 스레드가 {{ $value }}개 입니다"
  YAML
}

# ============================================================
# AMP Alertmanager 설정
# ============================================================

resource "aws_prometheus_alert_manager_definition" "main" {
  workspace_id = aws_prometheus_workspace.main.id

  definition = <<-YAML
    alertmanager_config: |
      global:
        resolve_timeout: 5m

      route:
        # 서비스+네임스페이스 단위로 묶어서 전송 (같은 서비스의 여러 alert → 하나로 묶음)
        group_by: ['alertname', 'service_name', 'service_namespace']
        group_wait: 30s       # 첫 발생 후 30초 대기 (추가 alert 묶기)
        group_interval: 5m    # 그룹에 새 alert 추가 시 대기
        repeat_interval: 4h   # 미해결 alert 반복 전송 간격 (기본)
        receiver: sns-alert

        routes:
          # Critical: 1시간마다 반복 (빠른 재알림)
          - match:
              severity: critical
            receiver: sns-alert
            group_wait: 30s
            group_interval: 5m
            repeat_interval: 1h

          # Warning: 12시간마다 반복 (노이즈 감소)
          - match:
              severity: warning
            receiver: sns-alert
            group_wait: 1m
            group_interval: 10m
            repeat_interval: 12h

      # Critical 발동 시 같은 서비스의 Warning 억제
      # (같은 장애에 대해 중복 알람 방지)
      inhibit_rules:
        - source_match:
            severity: critical
          target_match:
            severity: warning
          equal:
            - alertname
            - service_name
            - service_namespace

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
