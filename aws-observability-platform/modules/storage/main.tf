# ============================================================
# S3 Buckets + lifecycle
# ============================================================

resource "aws_s3_bucket" "logs_backup" {
  bucket = "${var.project_name}-logs-backup-${var.account_id}"
  tags   = { Name = "${var.project_name}-logs-backup" }
}

resource "aws_s3_bucket_lifecycle_configuration" "logs_backup" {
  bucket = aws_s3_bucket.logs_backup.id
  rule {
    id     = "delete-old-logs"
    status = "Enabled"
    filter { prefix = "" }
    expiration { days = 30 }
  }
}

resource "aws_s3_bucket" "traces_backup" {
  bucket = "${var.project_name}-traces-backup-${var.account_id}"
  tags   = { Name = "${var.project_name}-traces-backup" }
}

resource "aws_s3_bucket_lifecycle_configuration" "traces_backup" {
  bucket = aws_s3_bucket.traces_backup.id
  rule {
    id     = "delete-old-traces"
    status = "Enabled"
    filter { prefix = "" }
    expiration { days = 30 }
  }
}

resource "aws_s3_bucket" "metrics_backup" {
  bucket = "${var.project_name}-metrics-backup-${var.account_id}"
  tags   = { Name = "${var.project_name}-metrics-backup" }
}

resource "aws_s3_bucket_lifecycle_configuration" "metrics_backup" {
  bucket = aws_s3_bucket.metrics_backup.id
  rule {
    id     = "expire-old-metrics"
    status = "Enabled"
    expiration { days = 365 }
  }
}

resource "aws_s3_bucket" "runbooks" {
  bucket = "${var.project_name}-runbooks-${var.account_id}"
  tags   = { Name = "${var.project_name}-runbooks" }
}

resource "aws_s3_bucket" "deploy" {
  bucket = "${var.project_name}-deploy-${var.account_id}"
  tags   = { Name = "${var.project_name}-deploy" }
}

resource "aws_s3_bucket_versioning" "deploy" {
  bucket = aws_s3_bucket.deploy.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket" "athena_results" {
  bucket = "${var.project_name}-athena-results-${var.account_id}"
  tags   = { Name = "${var.project_name}-athena-results" }
}

resource "aws_s3_bucket_lifecycle_configuration" "athena_results" {
  bucket = aws_s3_bucket.athena_results.id
  rule {
    id     = "expire-query-results"
    status = "Enabled"
    expiration { days = 7 }
  }
}
