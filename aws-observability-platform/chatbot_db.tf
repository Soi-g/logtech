# ============================================================
# 챗봇 대화 저장소 — DynamoDB
# ============================================================

resource "aws_dynamodb_table" "chatbot_conversations" {
  name         = "${var.project_name}-chatbot-conversations"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "conversation_id"

  attribute {
    name = "conversation_id"
    type = "S"
  }

  tags = { Name = "${var.project_name}-chatbot-conversations" }
}

resource "aws_dynamodb_table" "chatbot_messages" {
  name         = "${var.project_name}-chatbot-messages"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "conversation_id"
  range_key    = "message_id"

  attribute {
    name = "conversation_id"
    type = "S"
  }
  attribute {
    name = "message_id"
    type = "S"
  }

  tags = { Name = "${var.project_name}-chatbot-messages" }
}

output "chatbot_conversations_table" {
  value = aws_dynamodb_table.chatbot_conversations.name
}

output "chatbot_messages_table" {
  value = aws_dynamodb_table.chatbot_messages.name
}
