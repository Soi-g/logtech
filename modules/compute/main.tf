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

resource "aws_iam_role_policy_attachment" "otel_collector_ssm" {
  role       = aws_iam_role.otel_collector.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# ============================================================
# Gateway OTel Collector — NLB + ASG
# ============================================================

locals {
  otel_user_data = templatefile("${path.root}/user_data.sh", {
    amp_remote_write_url        = var.amp_endpoint
    amp_workspace_id            = var.amp_workspace_id
    aws_region                  = var.aws_region
    s3_logs_bucket              = var.s3_logs_backup_id
    s3_traces_bucket            = var.s3_traces_backup_id
    s3_metrics_bucket           = var.s3_metrics_backup_id
    project_name                = var.project_name
    project_name_underscore     = replace(var.project_name, "-", "_")
    cognito_user_pool_id        = var.cognito_user_pool_id
    cognito_client_id           = var.cognito_client_id
    opensearch_endpoint         = var.opensearch_endpoint
    opensearch_master_user      = var.opensearch_master_user
    opensearch_master_password  = var.opensearch_master_password
    otel_collector_role_arn     = aws_iam_role.otel_collector.arn
    agentcore_memory_id         = var.agentcore_memory_id
    athena_results_bucket       = var.s3_athena_results_id
    logs_bucket                 = var.s3_logs_backup_id
    traces_bucket               = var.s3_traces_backup_id
    deploy_bucket               = var.s3_deploy_id
    chatbot_conversations_table = var.chatbot_conversations_table
    chatbot_messages_table      = var.chatbot_messages_table
  })
}

resource "aws_launch_template" "otel_gateway" {
  name_prefix = "${var.project_name}-otel-gw-"

  image_id      = var.ec2_ami_id
  instance_type = "t2.micro"

  iam_instance_profile {
    name = aws_iam_instance_profile.otel_collector.name
  }

  vpc_security_group_ids = [var.otel_collector_sg_id]

  block_device_mappings {
    device_name = "/dev/sda1"
    ebs {
      volume_size           = 8
      volume_type           = "gp3"
      delete_on_termination = true
    }
  }

  user_data = base64gzip(local.otel_user_data)

  update_default_version = true

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name = "${var.project_name}-otel-gateway"
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_lb_target_group" "otel_otlp_grpc" {
  name_prefix = substr(md5("${var.project_name}-grpc"), 0, 6)
  port        = 14317
  protocol    = "TCP"
  vpc_id      = var.vpc_id
  target_type = "instance"

  health_check {
    enabled             = true
    protocol            = "TCP"
    port                = "traffic-port"
    healthy_threshold   = 3
    unhealthy_threshold = 3
    interval            = 10
  }

  tags = { Name = "${var.project_name}-otel-tg-grpc" }
}

resource "aws_lb_target_group" "otel_otlp_http" {
  name_prefix = substr(md5("${var.project_name}-http"), 0, 6)
  port        = 14318
  protocol    = "TCP"
  vpc_id      = var.vpc_id
  target_type = "instance"

  health_check {
    enabled             = true
    protocol            = "TCP"
    port                = "traffic-port"
    healthy_threshold   = 3
    unhealthy_threshold = 3
    interval            = 10
  }

  tags = { Name = "${var.project_name}-otel-tg-http" }
}

resource "aws_lb" "otel_gateway" {
  name                       = substr("${var.project_name}-otel-nlb", 0, 32)
  load_balancer_type         = "network"
  internal                   = false
  subnets                    = var.public_subnet_ids
  enable_deletion_protection = false

  tags = { Name = "${var.project_name}-otel-nlb" }
}

resource "aws_lb_listener" "otlp_grpc" {
  load_balancer_arn = aws_lb.otel_gateway.arn
  port              = "4317"
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.otel_otlp_grpc.arn
  }
}

resource "aws_lb_listener" "otlp_http" {
  load_balancer_arn = aws_lb.otel_gateway.arn
  port              = "4318"
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.otel_otlp_http.arn
  }
}

resource "aws_autoscaling_group" "otel_gateway" {
  name                      = "${var.project_name}-otel-gateway-asg"
  vpc_zone_identifier       = var.public_subnet_ids
  min_size                  = 2
  max_size                  = 6
  desired_capacity          = 2
  health_check_type         = "ELB"
  health_check_grace_period = 300

  launch_template {
    id      = aws_launch_template.otel_gateway.id
    version = "$Latest"
  }

  target_group_arns = [
    aws_lb_target_group.otel_otlp_grpc.arn,
    aws_lb_target_group.otel_otlp_http.arn,
  ]

  tag {
    key                 = "Name"
    value               = "${var.project_name}-otel-gateway"
    propagate_at_launch = true
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_autoscaling_policy" "otel_network_in" {
  name                   = "${var.project_name}-otel-asg-netin"
  autoscaling_group_name = aws_autoscaling_group.otel_gateway.name
  policy_type            = "TargetTrackingScaling"

  target_tracking_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ASGAverageNetworkIn"
    }
    # 인스턴스당 평균 수신 바이트/초 — 환경에 맞게 조정
    target_value = 10000000
  }
}
