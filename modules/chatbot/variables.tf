variable "project_name" {
  type = string
}

variable "s3_deploy_id" {
  description = "배포 S3 버킷 ID (chatbot.zip 업로드 대상)"
  type        = string
}
