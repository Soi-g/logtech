# ============================================================
# Bedrock Agent Runtime + Memory 인프라
# ============================================================

# ============================================================
# DynamoDB - ongoing 인시던트 상태 추적 (Phase 4: AOSS 대체)
# ============================================================

resource "aws_dynamodb_table" "incident_ongoing" {
  name         = "${var.project_name}-incident-ongoing"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "alert_name"

  attribute {
    name = "alert_name"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  tags = { Name = "${var.project_name}-incident-ongoing" }
}

resource "aws_iam_role_policy" "lambda_dynamodb_incident" {
  name = "${var.project_name}-lambda-dynamodb-incident-policy"
  role = module.alerting.lambda_agent_role_id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "DynamoDBIncidentOngoing"
      Effect = "Allow"
      Action = [
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:DeleteItem",
        "dynamodb:UpdateItem",
      ]
      Resource = aws_dynamodb_table.incident_ongoing.arn
    }]
  })
}

# ============================================================
# Bedrock Agent 정의 (Strands Agents 사용)
# ============================================================
resource "aws_bedrockagent_agent" "observability" {
  agent_name              = "${var.project_name}-observability-agent"
  agent_resource_role_arn = module.observability.bedrock_agent_role_arn
  foundation_model        = "apac.anthropic.claude-3-5-sonnet-20241022-v2:0"

  description = "AWS 옵저버빌리티 플랫폼 AI 에이전트 - 장애 분석 및 대응"

  instruction = <<-EOT
당신은 AWS 옵저버빌리티 플랫폼의 장애 분석 AI 에이전트입니다.

역할:
1. 알람 발생 시 메트릭, 로그, 트레이스를 분석하여 근본 원인 파악
2. 과거 유사 장애 이력을 참조하여 빠른 해결 방법 제시
3. 즉시 조치 및 후속 조치 권장
4. 관련 런북 참조 (Knowledge Base에서 검색된 런북만 포함)

과거 장애 이력 활용:
- 동일 알람이 과거에 발생한 적이 있다면, 그때의 원인과 해결 방법을 우선 참조
- 평균 해결 시간과 가장 흔한 원인을 고려
- 새로운 패턴이 발견되면 명시적으로 언급

**CRITICAL - 런북 참조 규칙:**
- runbook_references는 ALWAYS 빈 배열 []로 반환하세요
- Knowledge Base가 비어있으므로 절대로 런북을 참조하지 마세요
- spring-boot-troubleshooting.md 같은 파일명을 만들어내지 마세요
- 런북 관련 내용은 일체 생성하지 마세요

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
  "runbook_references": []
}

runbook_references는 ALWAYS 빈 배열 []이어야 합니다. 절대로 런북을 만들어내지 마세요.
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
# ⚠️ 1단계에서 주석 처리, 3단계에서 주석 해제
# ============================================================

# resource "aws_bedrockagent_agent_knowledge_base_association" "runbooks" {
#   agent_id             = aws_bedrockagent_agent.observability.id
#   agent_version        = "DRAFT"
#   knowledge_base_id    = aws_bedrockagent_knowledge_base.runbooks.id
#   description          = "운영 런북 Knowledge Base"
#   knowledge_base_state = "ENABLED"
# }

# ============================================================
# IAM - AOSS 접근 권한 (runbooks만 유지, incident_memory 제거)
# ============================================================
resource "aws_iam_role_policy" "lambda_aoss_incident_memory" {
  name = "${var.project_name}-lambda-aoss-incident-memory-policy"
  role = module.alerting.lambda_agent_role_id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # {
      #   Sid      = "AOSSRunbooksAccess"
      #   Effect   = "Allow"
      #   Action   = ["aoss:APIAccessAll"]
      #   Resource = aws_opensearchserverless_collection.runbooks.arn
      # },
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

output "dynamodb_incident_table" {
  value       = aws_dynamodb_table.incident_ongoing.name
  description = "DynamoDB ongoing 인시던트 추적 테이블"
}
