# ============================================================
# AgentCore Memory - Phase 1 기반 설정
#
# Memory Store 생성 (최초 1회 수동 실행):
#   aws bedrock-agentcore-control create-memory \
#     --name "log-platform-dev-agentcore-memory" \
#     --description "Observability platform incident history" \
#     --event-expiry-duration 90 \
#     --memory-strategies '[{"semanticMemoryStrategy":{"name":"incidents","namespaces":["/incidents/"]}}]' \
#     --region ap-northeast-2 \
#     --query 'memory.id' --output text
#
# 출력된 ID를 terraform.tfvars에 추가:
#   agentcore_memory_id = "mem-xxxxxxxxxxxx"
# ============================================================

# ============================================================
# IAM - Lambda가 AgentCore Memory를 읽고 쓸 수 있는 권한
# ============================================================

resource "aws_iam_role_policy" "lambda_agentcore_memory" {
  name = "${var.project_name}-lambda-agentcore-memory-policy"
  role = aws_iam_role.lambda_agent.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AgentCoreMemoryData"
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:CreateEvent",
          "bedrock-agentcore:BatchCreateMemoryRecords",
          "bedrock-agentcore:RetrieveMemoryRecords",
          "bedrock-agentcore:DeleteMemoryRecord",
          "bedrock-agentcore:BatchDeleteMemoryRecords",
          "bedrock-agentcore:GetMemoryRecord",
          "bedrock-agentcore:ListMemoryRecords",
        ]
        Resource = "*"
      },
      {
        Sid    = "AgentCoreMemoryControl"
        Effect = "Allow"
        Action = [
          "bedrock-agentcore-control:GetMemory",
          "bedrock-agentcore-control:ListMemories",
        ]
        Resource = "*"
      }
    ]
  })
}

# ============================================================
# Outputs
# ============================================================

output "agentcore_memory_id" {
  value       = var.agentcore_memory_id
  description = "Bedrock AgentCore Memory Store ID (terraform.tfvars에서 설정)"
}
