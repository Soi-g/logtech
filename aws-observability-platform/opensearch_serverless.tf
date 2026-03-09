# ============================================================
# OpenSearch Serverless - Bedrock Knowledge Base 벡터 스토어
# ============================================================

# 암호화 정책
resource "aws_opensearchserverless_security_policy" "runbooks_encryption" {
  name        = "${var.project_name}-runbooks-enc"
  type        = "encryption"
  description = "런북 벡터 검색 암호화 정책"

  policy = jsonencode({
    Rules = [{
      ResourceType = "collection"
      Resource     = ["collection/${var.project_name}-runbooks"]
    }]
    AWSOwnedKey = true
  })
}

# 네트워크 정책
resource "aws_opensearchserverless_security_policy" "runbooks_network" {
  name        = "${var.project_name}-runbooks-net"
  type        = "network"
  description = "런북 벡터 검색 네트워크 정책"

  policy = jsonencode([{
    Rules = [
      {
        ResourceType = "collection"
        Resource     = ["collection/${var.project_name}-runbooks"]
      },
      {
        ResourceType = "dashboard"
        Resource     = ["collection/${var.project_name}-runbooks"]
      }
    ]
    AllowFromPublic = true
  }])
}

# 데이터 접근 정책 - Bedrock KB 역할 + Lambda 역할 허용
resource "aws_opensearchserverless_access_policy" "runbooks" {
  name        = "${var.project_name}-runbooks-access"
  type        = "data"
  description = "런북 벡터 검색 접근 정책"

  policy = jsonencode([{
    Rules = [
      {
        ResourceType = "index"
        Resource     = ["index/${var.project_name}-runbooks/*"]
        Permission   = ["aoss:*"]
      },
      {
        ResourceType = "collection"
        Resource     = ["collection/${var.project_name}-runbooks"]
        Permission   = ["aoss:*"]
      }
    ]
    Principal = [
      aws_iam_role.lambda_agent.arn,
      aws_iam_role.bedrock_agent.arn,
      aws_iam_role.bedrock_kb.arn,
      aws_iam_role.lambda_kb_sync.arn,
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
    ]
  }])
}

# OpenSearch Serverless 컬렉션
resource "aws_opensearchserverless_collection" "runbooks" {
  name        = "${var.project_name}-runbooks"
  type        = "VECTORSEARCH"
  description = "런북 벡터 검색 컬렉션"

  tags = { Name = "${var.project_name}-runbooks-vector" }

  depends_on = [
    aws_opensearchserverless_security_policy.runbooks_encryption,
    aws_opensearchserverless_security_policy.runbooks_network,
    aws_opensearchserverless_access_policy.runbooks,
  ]
}

# ============================================================
# AOSS 인덱스 생성 Lambda (KB용)
# ============================================================

resource "aws_lambda_function" "aoss_index_creator" {
  function_name    = "${var.project_name}-aoss-index-creator"
  role             = aws_iam_role.lambda_agent.arn
  handler          = "aoss_index_creator.handler"
  runtime          = "python3.12"
  filename         = data.archive_file.lambda_agent.output_path
  source_code_hash = data.archive_file.lambda_agent.output_base64sha256
  timeout          = 60
  memory_size      = 256

  tags = { Name = "${var.project_name}-aoss-index-creator" }
}

# Lambda 호출로 인덱스 생성 (수동 실행 필요)
# AOSS 정책 전파에 최대 5분 소요되므로 terraform apply 후 수동으로 실행
resource "null_resource" "create_kb_index" {
  triggers = {
    collection_endpoint = aws_opensearchserverless_collection.runbooks.collection_endpoint
    lambda_hash         = aws_lambda_function.aoss_index_creator.source_code_hash
  }

  # 자동 실행 비활성화 - 수동으로 실행 필요
  # provisioner "local-exec" {
  #   command = "echo '인덱스는 수동으로 생성하세요'"
  # }

  depends_on = [
    aws_opensearchserverless_collection.runbooks,
    aws_opensearchserverless_access_policy.runbooks,
    aws_lambda_function.aoss_index_creator
  ]
}

# ============================================================
# Bedrock Knowledge Base IAM 역할
# ============================================================

resource "aws_iam_role" "bedrock_kb" {
  name = "${var.project_name}-bedrock-kb-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "bedrock.amazonaws.com" }
      Condition = {
        StringEquals = {
          "aws:SourceAccount" = data.aws_caller_identity.current.account_id
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "bedrock_kb" {
  name = "${var.project_name}-bedrock-kb-policy"
  role = aws_iam_role.bedrock_kb.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "S3RunbooksAccess"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:ListBucket"]
        Resource = [
          aws_s3_bucket.runbooks.arn,
          "${aws_s3_bucket.runbooks.arn}/*"
        ]
      },
      {
        Sid      = "BedrockEmbedding"
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel"]
        Resource = "arn:aws:bedrock:${var.aws_region}::foundation-model/amazon.titan-embed-text-v2:0"
      },
      {
        Sid      = "AOSSAccess"
        Effect   = "Allow"
        Action   = ["aoss:APIAccessAll"]
        Resource = aws_opensearchserverless_collection.runbooks.arn
      }
    ]
  })
}

# ============================================================
# Bedrock Knowledge Base
# ============================================================

resource "aws_bedrockagent_knowledge_base" "runbooks" {
  name        = "${var.project_name}-runbooks-kb"
  role_arn    = aws_iam_role.bedrock_kb.arn
  description = "운영 런북 Knowledge Base - RAG 기반 대응 절차 검색"

  knowledge_base_configuration {
    type = "VECTOR"
    vector_knowledge_base_configuration {
      embedding_model_arn = "arn:aws:bedrock:${var.aws_region}::foundation-model/amazon.titan-embed-text-v2:0"
    }
  }

  storage_configuration {
    type = "OPENSEARCH_SERVERLESS"
    opensearch_serverless_configuration {
      collection_arn    = aws_opensearchserverless_collection.runbooks.arn
      vector_index_name = "bedrock-knowledge-base-default-index"
      field_mapping {
        vector_field   = "bedrock-knowledge-base-default-vector"
        text_field     = "AMAZON_BEDROCK_TEXT_CHUNK"
        metadata_field = "AMAZON_BEDROCK_METADATA"
      }
    }
  }

  depends_on = [
    aws_iam_role_policy.bedrock_kb,
    aws_opensearchserverless_access_policy.runbooks,
    null_resource.create_kb_index
  ]
}

# ============================================================
# Bedrock Knowledge Base 데이터 소스 (S3)
# ============================================================

resource "aws_bedrockagent_data_source" "runbooks" {
  knowledge_base_id = aws_bedrockagent_knowledge_base.runbooks.id
  name              = "${var.project_name}-runbooks-s3"
  description       = "S3 런북 마크다운 데이터 소스"

  data_source_configuration {
    type = "S3"
    s3_configuration {
      bucket_arn         = aws_s3_bucket.runbooks.arn
      inclusion_prefixes = ["runbooks/"]
    }
  }

  vector_ingestion_configuration {
    chunking_configuration {
      chunking_strategy = "FIXED_SIZE"
      fixed_size_chunking_configuration {
        max_tokens         = 300
        overlap_percentage = 20
      }
    }
  }
}

# ============================================================
# S3 → EventBridge 이벤트 활성화
# ============================================================

resource "aws_s3_bucket_notification" "runbooks" {
  bucket      = aws_s3_bucket.runbooks.id
  eventbridge = true
}

# ============================================================
# KB 동기화 Lambda IAM
# ============================================================

resource "aws_iam_role" "lambda_kb_sync" {
  name = "${var.project_name}-lambda-kb-sync-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "lambda_kb_sync" {
  name = "${var.project_name}-lambda-kb-sync-policy"
  role = aws_iam_role.lambda_kb_sync.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "BedrockKBSync"
        Effect = "Allow"
        Action = ["bedrock:StartIngestionJob", "bedrock:GetIngestionJob"]
        Resource = aws_bedrockagent_knowledge_base.runbooks.arn
      },
      {
        Sid      = "CloudWatchLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*"
      }
    ]
  })
}

# ============================================================
# KB 동기화 Lambda
# ============================================================

resource "aws_lambda_function" "kb_sync" {
  function_name    = "${var.project_name}-kb-sync"
  role             = aws_iam_role.lambda_kb_sync.arn
  handler          = "runbooks_aws.indexing_handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 128
  layers           = [aws_lambda_layer_version.agent_deps.arn]

  filename         = data.archive_file.lambda_agent.output_path
  source_code_hash = data.archive_file.lambda_agent.output_base64sha256

  environment {
    variables = {
      BEDROCK_KB_ID             = aws_bedrockagent_knowledge_base.runbooks.id
      BEDROCK_KB_DATA_SOURCE_ID = aws_bedrockagent_data_source.runbooks.data_source_id
      AWS_REGION_NAME           = var.aws_region
    }
  }

  tags = { Name = "${var.project_name}-kb-sync" }
}

# ============================================================
# EventBridge Rule - S3 runbooks/*.md 업로드 감지
# ============================================================

resource "aws_cloudwatch_event_rule" "runbooks_s3_upload" {
  name        = "${var.project_name}-runbooks-upload"
  description = "S3 런북 업로드 감지 → KB 동기화"

  event_pattern = jsonencode({
    source      = ["aws.s3"]
    "detail-type" = ["Object Created"]
    detail = {
      bucket = { name = [aws_s3_bucket.runbooks.id] }
      object = { key = [{ prefix = "runbooks/" }] }
    }
  })
}

resource "aws_cloudwatch_event_target" "kb_sync_lambda" {
  rule      = aws_cloudwatch_event_rule.runbooks_s3_upload.name
  target_id = "KBSyncLambda"
  arn       = aws_lambda_function.kb_sync.arn
}

resource "aws_lambda_permission" "eventbridge_kb_sync" {
  statement_id  = "AllowEventBridgeTrigger"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.kb_sync.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.runbooks_s3_upload.arn
}

# ============================================================
# Lambda IAM - Bedrock KB 검색 권한 (분석 에이전트용)
# ============================================================

resource "aws_iam_role_policy" "lambda_bedrock_kb" {
  name = "${var.project_name}-lambda-bedrock-kb-policy"
  role = aws_iam_role.lambda_agent.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "BedrockKBRetrieve"
        Effect   = "Allow"
        Action   = ["bedrock:Retrieve", "bedrock:RetrieveAndGenerate"]
        Resource = aws_bedrockagent_knowledge_base.runbooks.arn
      },
      {
        Sid      = "BedrockEmbedding"
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel"]
        Resource = "arn:aws:bedrock:${var.aws_region}::foundation-model/amazon.titan-embed-text-v2:0"
      },
      {
        Sid    = "S3Runbooks"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:ListBucket"]
        Resource = [
          aws_s3_bucket.runbooks.arn,
          "${aws_s3_bucket.runbooks.arn}/*"
        ]
      }
    ]
  })
}

# ============================================================
# Outputs
# ============================================================

output "knowledge_base_id" {
  value       = aws_bedrockagent_knowledge_base.runbooks.id
  description = "Bedrock Knowledge Base ID"
}

output "knowledge_base_data_source_id" {
  value       = aws_bedrockagent_data_source.runbooks.data_source_id
  description = "Knowledge Base 데이터 소스 ID"
}
