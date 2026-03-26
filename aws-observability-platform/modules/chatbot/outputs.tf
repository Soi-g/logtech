output "conversations_table" {
  value = aws_dynamodb_table.conversations.name
}

output "messages_table" {
  value = aws_dynamodb_table.messages.name
}

# EC2 compute 모듈이 이 etag를 변수로 받아 chatbot 패키지 업로드 완료 후 EC2가 뜨도록 순서 보장
output "package_etag" {
  value = aws_s3_object.package.etag
}
