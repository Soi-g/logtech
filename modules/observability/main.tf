# ============================================================
# SNS Topic (AMP Alertmanager → Lambda)
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
# Amazon Managed Prometheus (AMP)
# ============================================================

resource "aws_prometheus_workspace" "main" {
  alias = "${var.project_name}-amp"
  tags  = { Name = "${var.project_name}-amp" }
}

resource "aws_prometheus_rule_group_namespace" "derived_metrics" {
  name         = "derived-metrics"
  workspace_id = aws_prometheus_workspace.main.id
  data         = file("${path.module}/prometheus-rules.yaml")
}

resource "aws_prometheus_rule_group_namespace" "alerts" {
  name         = "observability-alerts"
  workspace_id = aws_prometheus_workspace.main.id
  data         = file("${path.module}/prometheus-alerts.yaml")
}

resource "aws_prometheus_alert_manager_definition" "main" {
  workspace_id = aws_prometheus_workspace.main.id

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

  depends_on = [aws_prometheus_rule_group_namespace.alerts]
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
    subnet_ids         = [var.private_subnet_id]
    security_group_ids = [var.opensearch_sg_id]
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
        Resource  = "arn:aws:es:${var.aws_region}:${var.account_id}:domain/${var.project_name}/*"
      }
    ]
  })

  tags = { Name = "${var.project_name}-opensearch" }
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
        Resource = [var.s3_runbooks_arn, "${var.s3_runbooks_arn}/*"]
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
# Glue Database + Tables (S3 → Athena 장기 분석)
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
        var.s3_logs_backup_arn,
        "${var.s3_logs_backup_arn}/*",
        var.s3_traces_backup_arn,
        "${var.s3_traces_backup_arn}/*",
        var.s3_metrics_backup_arn,
        "${var.s3_metrics_backup_arn}/*",
      ]
    }]
  })
}

locals {
  log_columns_type = "array<struct<resource:struct<attributes:array<struct<key:string,value:struct<stringvalue:string,intvalue:string,boolvalue:boolean,doublevalue:double>>>>,scopelogs:array<struct<logrecords:array<struct<timeunixnano:string,observedtimeunixnano:string,severitytext:string,severitynumber:int,body:struct<stringvalue:string>,attributes:array<struct<key:string,value:struct<stringvalue:string,intvalue:string>>>,traceid:string,spanid:string>>>>>>"
  time_projection = {
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
    "storage.location.template" = "s3://${var.s3_logs_backup_id}/$${deployment_environment}/app/year=$${year}/month=$${month}/day=$${day}/hour=$${hour}/minute=$${minute}"
  })

  storage_descriptor {
    location      = "s3://${var.s3_logs_backup_id}/"
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
    "storage.location.template" = "s3://${var.s3_logs_backup_id}/$${deployment_environment}/host/year=$${year}/month=$${month}/day=$${day}/hour=$${hour}/minute=$${minute}"
  })

  storage_descriptor {
    location      = "s3://${var.s3_logs_backup_id}/"
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
    "storage.location.template" = "s3://${var.s3_traces_backup_id}/$${deployment_environment}/app/year=$${year}/month=$${month}/day=$${day}/hour=$${hour}/minute=$${minute}"
  })

  storage_descriptor {
    location      = "s3://${var.s3_traces_backup_id}/"
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
    "classification"            = "json"
    "projection.source.type"   = "enum"
    "projection.source.values" = "app,container,host"
    "storage.location.template" = "s3://${var.s3_metrics_backup_id}/$${deployment_environment}/$${source}/year=$${year}/month=$${month}/day=$${day}/hour=$${hour}/minute=$${minute}"
  })

  storage_descriptor {
    location      = "s3://${var.s3_metrics_backup_id}/"
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
# Athena Workgroup
# ============================================================

resource "aws_athena_workgroup" "observability" {
  name          = "${var.project_name}-observability"
  force_destroy = true

  configuration {
    result_configuration {
      output_location = "s3://${var.s3_athena_results_id}/query-results/"
    }
  }

  tags = { Name = "${var.project_name}-athena-workgroup" }
}

# ============================================================
# Cognito - 고객 OTel Collector 인증
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

resource "aws_cognito_user_pool_domain" "otel_auth" {
  domain       = "${var.project_name}-${var.account_id}-otel-auth"
  user_pool_id = aws_cognito_user_pool.otel_auth.id
}

resource "aws_cognito_resource_server" "otel" {
  identifier   = "https://${var.project_name}.otel"
  name         = "${var.project_name}-otel-resource-server"
  user_pool_id = aws_cognito_user_pool.otel_auth.id

  scope {
    scope_name        = "ingest"
    scope_description = "OTel 데이터 수집 권한"
  }
}

resource "aws_cognito_user_pool_client" "otel_customer" {
  name         = "${var.project_name}-otel-customer-client"
  user_pool_id = aws_cognito_user_pool.otel_auth.id

  allowed_oauth_flows                  = ["client_credentials"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["${aws_cognito_resource_server.otel.identifier}/ingest"]
  generate_secret                      = true

  depends_on = [aws_cognito_resource_server.otel]
}
