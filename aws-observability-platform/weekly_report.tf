# ============================================================
# 주간 옵저버빌리티 리포트
# ============================================================

# Lambda 함수
resource "aws_lambda_function" "weekly_report" {
  function_name    = "${var.project_name}-weekly-report"
  role             = aws_iam_role.lambda_agent.arn
  handler          = "weekly_report.handler"
  runtime          = "python3.12"
  filename         = data.archive_file.lambda_agent.output_path
  source_code_hash = data.archive_file.lambda_agent.output_base64sha256
  timeout          = 300
  memory_size      = 512

  environment {
    variables = {
      GLUE_DATABASE            = aws_glue_catalog_database.observability.name
      ATHENA_OUTPUT_LOCATION   = "s3://${aws_s3_bucket.athena_results.id}/"
      SLACK_BOT_TOKEN          = var.slack_bot_token
      SLACK_CHANNEL            = var.slack_channel
      AWS_REGION_NAME          = var.aws_region
    }
  }

  tags = { Name = "${var.project_name}-weekly-report" }
}

# EventBridge 스케줄 - 매주 월요일 오전 9시 (UTC 0시)
resource "aws_cloudwatch_event_rule" "weekly_report" {
  name                = "${var.project_name}-weekly-report"
  description         = "매주 월요일 오전 9시 주간 리포트 생성"
  schedule_expression = "cron(0 0 ? * MON *)"  # 매주 월요일 00:00 UTC (한국 시간 09:00)
}

resource "aws_cloudwatch_event_target" "weekly_report" {
  rule      = aws_cloudwatch_event_rule.weekly_report.name
  target_id = "WeeklyReportLambda"
  arn       = aws_lambda_function.weekly_report.arn
}

resource "aws_lambda_permission" "weekly_report" {
  statement_id  = "AllowEventBridgeWeeklyReport"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.weekly_report.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.weekly_report.arn
}

# Athena 쿼리 권한 추가
resource "aws_iam_role_policy" "lambda_athena" {
  name = "${var.project_name}-lambda-athena-policy"
  role = aws_iam_role.lambda_agent.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AthenaQuery"
        Effect = "Allow"
        Action = [
          "athena:StartQueryExecution",
          "athena:GetQueryExecution",
          "athena:GetQueryResults",
          "athena:StopQueryExecution"
        ]
        Resource = "*"
      },
      {
        Sid    = "GlueAccess"
        Effect = "Allow"
        Action = [
          "glue:GetDatabase",
          "glue:GetTable",
          "glue:GetPartitions"
        ]
        Resource = [
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:catalog",
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:database/${aws_glue_catalog_database.observability.name}",
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${aws_glue_catalog_database.observability.name}/*"
        ]
      },
      {
        Sid    = "S3AthenaResults"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket"
        ]
        Resource = [
          "${aws_s3_bucket.runbooks.arn}/athena-results/*",
          aws_s3_bucket.runbooks.arn
        ]
      },
      {
        Sid    = "S3DataAccess"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:ListBucket"]
        Resource = [
          "${aws_s3_bucket.logs_backup.arn}/*",
          "${aws_s3_bucket.metrics_backup.arn}/*",
          "${aws_s3_bucket.traces_backup.arn}/*",
          aws_s3_bucket.logs_backup.arn,
          aws_s3_bucket.metrics_backup.arn,
          aws_s3_bucket.traces_backup.arn
        ]
      }
    ]
  })
}

# ============================================================
# Outputs
# ============================================================

output "weekly_report_function_name" {
  value       = aws_lambda_function.weekly_report.function_name
  description = "주간 리포트 Lambda 함수명"
}

output "weekly_report_schedule" {
  value       = "매주 월요일 오전 9시 (KST)"
  description = "주간 리포트 스케줄"
}

output "weekly_report_test_command" {
  value = <<-EOT
    # 주간 리포트 수동 실행 (테스트용)
    aws lambda invoke \
      --function-name ${aws_lambda_function.weekly_report.function_name} \
      --region ${var.aws_region} \
      /tmp/weekly_report_response.json && cat /tmp/weekly_report_response.json
  EOT
  description = "주간 리포트 수동 실행 명령어"
}
