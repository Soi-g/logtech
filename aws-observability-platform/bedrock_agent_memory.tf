# ============================================================
# Bedrock Agent Runtime + Memory 인프라
# ============================================================

# ============================================================
# OpenSearch Serverless - 장애 이력 저장 (장기 메모리)
# ============================================================

# 장애 이력용 AOSS 컬렉션 (런북과 분리)
resource "aws_opensearchserverless_collection" "incident_memory" {
  name        = "${var.project_name}-incident-memory"
  type        = "VECTORSEARCH"
  description = "장애 이력 벡터 검색 컬렉션"

  depends_on = [
    aws_opensearchserverless_security_policy.incident_memory_encryption,
    aws_opensearchserverless_security_policy.incident_memory_network
  ]

  tags = {
    Name = "${var.project_name}-incident-memory"
  }
}

# 암호화 정책
resource "aws_opensearchserverless_security_policy" "incident_memory_encryption" {
  name = "${var.project_name}-incident-enc"
  type = "encryption"
  policy = jsonencode({
    Rules = [
      {
        ResourceType = "collection"
        Resource     = ["collection/${var.project_name}-incident-memory"]
      }
    ]
    AWSOwnedKey = true
  })
}

# 네트워크 정책
resource "aws_opensearchserverless_security_policy" "incident_memory_network" {
  name = "${var.project_name}-incident-net"
  type = "network"
  policy = jsonencode([
    {
      Rules = [
        {
          ResourceType = "collection"
          Resource     = ["collection/${var.project_name}-incident-memory"]
        },
        {
          ResourceType = "dashboard"
          Resource     = ["collection/${var.project_name}-incident-memory"]
        }
      ]
      AllowFromPublic = true
    }
  ])
}

# 데이터 접근 정책
resource "aws_opensearchserverless_access_policy" "incident_memory" {
  name = "${var.project_name}-incident-access"
  type = "data"
  policy = jsonencode([{
    Rules = [
      {
        ResourceType = "index"
        Resource     = ["index/${var.project_name}-incident-memory/*"]
        Permission   = ["aoss:*"]
      },
      {
        ResourceType = "collection"
        Resource     = ["collection/${var.project_name}-incident-memory"]
        Permission   = ["aoss:*"]
      }
    ]
    Principal = [
      aws_iam_role.lambda_agent.arn,
      aws_iam_role.bedrock_agent.arn,
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
    ]
  }])
  
  depends_on = [aws_opensearchserverless_collection.incident_memory]
}

# 인덱스 생성 Lambda
resource "aws_lambda_function" "incident_memory_index_creator" {
  function_name    = "${var.project_name}-incident-memory-index-creator"
  role             = aws_iam_role.lambda_agent.arn
  handler          = "incident_memory_index_creator.handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  layers           = [aws_lambda_layer_version.agent_deps.arn]

  filename         = data.archive_file.lambda_agent.output_path
  source_code_hash = data.archive_file.lambda_agent.output_base64sha256

  environment {
    variables = {
      AOSS_ENDPOINT = aws_opensearchserverless_collection.incident_memory.collection_endpoint
      AWS_REGION_NAME = var.aws_region
    }
  }

  tags = { Name = "${var.project_name}-incident-memory-index-creator" }
}

# 인덱스 자동 생성
resource "null_resource" "create_incident_memory_index" {
  provisioner "local-exec" {
    command = <<-EOT
      sleep 60
      aws lambda invoke \
        --function-name ${aws_lambda_function.incident_memory_index_creator.function_name} \
        --region ${var.aws_region} \
        --cli-binary-format raw-in-base64-out \
        --payload '{"endpoint":"${aws_opensearchserverless_collection.incident_memory.collection_endpoint}","region":"${var.aws_region}","index_name":"incident-memory-index"}' \
        /tmp/incident_memory_index_response.json || true
    EOT
  }

  triggers = {
    collection_endpoint = aws_opensearchserverless_collection.incident_memory.collection_endpoint
    lambda_hash         = aws_lambda_function.incident_memory_index_creator.source_code_hash
  }

  depends_on = [
    aws_opensearchserverless_collection.incident_memory,
    aws_opensearchserverless_access_policy.incident_memory,
    aws_lambda_function.incident_memory_index_creator
  ]
}

# ============================================================
# Bedrock Agent 정의 (Strands Agents 사용)
# ============================================================
resource "aws_bedrockagent_agent" "observability" {
  agent_name              = "${var.project_name}-observability-agent"
  agent_resource_role_arn = aws_iam_role.bedrock_agent.arn
  foundation_model        = "apac.anthropic.claude-3-5-sonnet-20241022-v2:0"
  
  description = "AWS 옵저버빌리티 플랫폼 AI 에이전트 - 장애 분석 및 대응"

  instruction = <<-EOT
당신은 AWS 옵저버빌리티 플랫폼의 장애 분석 AI 에이전트입니다.

역할:
1. 알람 발생 시 메트릭, 로그, 트레이스를 분석하여 근본 원인 파악
2. 과거 유사 장애 이력을 참조하여 빠른 해결 방법 제시
3. 즉시 조치 및 후속 조치 권장
4. 관련 런북 참조

과거 장애 이력 활용:
- 동일 알람이 과거에 발생한 적이 있다면, 그때의 원인과 해결 방법을 우선 참조
- 평균 해결 시간과 가장 흔한 원인을 고려
- 새로운 패턴이 발견되면 명시적으로 언급

**중요: 반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트는 포함하지 마세요.**

{
  "incident_summary": "한 문장 요약",
  "is_recurring": true,
  "past_occurrences": 0,
  "likely_root_causes": ["원인1", "원인2"],
  "severity": "critical",
  "impact": "영향 범위 설명",
  "immediate_actions": ["즉시 조치1", "즉시 조치2"],
  "follow_up_actions": ["후속 조치1"],
  "evidence_summary": ["근거1", "근거2"],
  "runbook_references": [{"source": "파일명", "section": "섹션", "relevance": "관련성"}]
}
EOT

  # 메모리 활성화
  memory_configuration {
    enabled_memory_types = ["SESSION_SUMMARY"]
    storage_days         = 30
  }

  idle_session_ttl_in_seconds = 3600

  tags = {
    Name = "${var.project_name}-observability-agent"
  }
}

# ============================================================
# Bedrock Agent Alias (배포 버전)
# ============================================================
resource "aws_bedrockagent_agent_alias" "prod" {
  agent_id         = aws_bedrockagent_agent.observability.id
  agent_alias_name = "prod"
  description      = "Production alias"
}

# ============================================================
# Knowledge Base 연결
# ============================================================
resource "aws_bedrockagent_agent_knowledge_base_association" "runbooks" {
  agent_id             = aws_bedrockagent_agent.observability.id
  agent_version        = "DRAFT"
  knowledge_base_id    = aws_bedrockagent_knowledge_base.runbooks.id
  description          = "운영 런북 Knowledge Base"
  knowledge_base_state = "ENABLED"
}

# ============================================================
# IAM - AOSS 접근 권한
# ============================================================
resource "aws_iam_role_policy" "lambda_aoss_incident_memory" {
  name = "${var.project_name}-lambda-aoss-incident-memory-policy"
  role = aws_iam_role.lambda_agent.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AOSSIncidentMemoryAccess"
        Effect = "Allow"
        Action = [
          "aoss:APIAccessAll"
        ]
        Resource = aws_opensearchserverless_collection.incident_memory.arn
      },
      {
        Sid    = "BedrockEmbeddings"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel"
        ]
        Resource = "arn:aws:bedrock:${var.aws_region}::foundation-model/amazon.titan-embed-text-v2:0"
      },
      {
        Sid    = "BedrockAgentRuntime"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeAgent"
        ]
        Resource = [
          "${aws_bedrockagent_agent.observability.agent_arn}",
          "arn:aws:bedrock:${var.aws_region}:${data.aws_caller_identity.current.account_id}:agent-alias/${aws_bedrockagent_agent.observability.id}/*"
        ]
      }
    ]
  })
}

# ============================================================
# Lambda - Bedrock Agent Runtime 호출 (SNS 트리거)
# ============================================================
resource "aws_lambda_function" "agent_runtime" {
  function_name    = "${var.project_name}-agent-runtime"
  role             = aws_iam_role.lambda_agent.arn
  handler          = "bedrock_agent_memory.lambda_handler"
  runtime          = "python3.12"
  timeout          = 120
  memory_size      = 512
  layers           = [aws_lambda_layer_version.agent_deps.arn]

  filename         = data.archive_file.lambda_agent.output_path
  source_code_hash = data.archive_file.lambda_agent.output_base64sha256

  environment {
    variables = {
      BEDROCK_AGENT_ID              = aws_bedrockagent_agent.observability.id
      BEDROCK_AGENT_ALIAS_ID        = aws_bedrockagent_agent_alias.prod.agent_alias_id
      AOSS_INCIDENT_MEMORY_ENDPOINT = aws_opensearchserverless_collection.incident_memory.collection_endpoint
      SLACK_BOT_TOKEN               = var.slack_bot_token
      SLACK_CHANNEL                 = var.slack_channel
      AWS_REGION_NAME               = var.aws_region
    }
  }

  tags = { Name = "${var.project_name}-agent-runtime" }
}

resource "aws_iam_role_policy" "lambda_bedrock_agent_runtime" {
  name = "${var.project_name}-lambda-bedrock-agent-runtime-policy"
  role = aws_iam_role.lambda_agent.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "BedrockAgentRuntime"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeAgent",
          "bedrock:Retrieve",
          "bedrock:RetrieveAndGenerate"
        ]
        Resource = [
          aws_bedrockagent_agent.observability.agent_arn,
          "${aws_bedrockagent_agent.observability.agent_arn}/*"
        ]
      }
    ]
  })
}

# ============================================================
# Outputs
# ============================================================
output "bedrock_agent_id" {
  value       = aws_bedrockagent_agent.observability.id
  description = "Bedrock Agent ID"
}

output "bedrock_agent_alias_id" {
  value       = aws_bedrockagent_agent_alias.prod.agent_alias_id
  description = "Bedrock Agent Alias ID (prod)"
}

output "incident_memory_endpoint" {
  value       = aws_opensearchserverless_collection.incident_memory.collection_endpoint
  description = "OpenSearch Serverless 장애 이력 엔드포인트"
}
