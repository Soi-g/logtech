output "logs_backup_id" {
  value = aws_s3_bucket.logs_backup.id
}

output "logs_backup_arn" {
  value = aws_s3_bucket.logs_backup.arn
}

output "traces_backup_id" {
  value = aws_s3_bucket.traces_backup.id
}

output "traces_backup_arn" {
  value = aws_s3_bucket.traces_backup.arn
}

output "metrics_backup_id" {
  value = aws_s3_bucket.metrics_backup.id
}

output "metrics_backup_arn" {
  value = aws_s3_bucket.metrics_backup.arn
}

output "runbooks_id" {
  value = aws_s3_bucket.runbooks.id
}

output "runbooks_arn" {
  value = aws_s3_bucket.runbooks.arn
}

output "deploy_id" {
  value = aws_s3_bucket.deploy.id
}

output "deploy_arn" {
  value = aws_s3_bucket.deploy.arn
}

output "athena_results_id" {
  value = aws_s3_bucket.athena_results.id
}

output "athena_results_arn" {
  value = aws_s3_bucket.athena_results.arn
}
