# ============================================================
# DynamoDB - 챗봇 대화 저장소
# ============================================================

resource "aws_dynamodb_table" "conversations" {
  name         = "${var.project_name}-chatbot-conversations"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "conversation_id"

  attribute {
    name = "conversation_id"
    type = "S"
  }

  tags = { Name = "${var.project_name}-chatbot-conversations" }
}

resource "aws_dynamodb_table" "messages" {
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

# ============================================================
# 챗봇 코드 패키징 → S3 업로드
# chatbot/ 소스는 프로젝트 루트에 위치 (path.root 사용)
# ============================================================

data "archive_file" "package" {
  type        = "zip"
  output_path = "${path.root}/chatbot_package.zip"

  source {
    content  = file("${path.module}/chatbot/app.py")
    filename = "chatbot/app.py"
  }
  source {
    content  = file("${path.module}/chatbot/chat_agent.py")
    filename = "chatbot/chat_agent.py"
  }
  source {
    content  = file("${path.module}/chatbot/database.py")
    filename = "chatbot/database.py"
  }
  source {
    content  = file("${path.module}/chatbot/requirements.txt")
    filename = "chatbot/requirements.txt"
  }
  source {
    content  = file("${path.module}/chatbot/templates/index.html")
    filename = "chatbot/templates/index.html"
  }
  source {
    content  = file("${path.root}/lambda_package/agents_aws.py")
    filename = "lambda_package/agents_aws.py"
  }
  source {
    content  = file("${path.root}/lambda_package/agentcore_memory.py")
    filename = "lambda_package/agentcore_memory.py"
  }
}

resource "aws_s3_object" "package" {
  bucket = var.s3_deploy_id
  key    = "chatbot/chatbot.zip"
  source = data.archive_file.package.output_path
  etag   = data.archive_file.package.output_md5
}
