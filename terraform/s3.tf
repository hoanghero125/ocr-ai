resource "aws_s3_bucket" "results" {
  bucket = "${local.prefix}-results"
  tags   = local.common_tags
}

resource "aws_s3_bucket_public_access_block" "results" {
  bucket                  = aws_s3_bucket.results.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "results" {
  bucket = aws_s3_bucket.results.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "results" {
  bucket = aws_s3_bucket.results.id

  rule {
    id     = "expire-checkpoints"
    status = "Enabled"

    filter {
      prefix = "checkpoints/"
    }

    expiration {
      days = 7
    }
  }

  rule {
    id     = "expire-results"
    status = "Enabled"

    filter {
      prefix = "results/"
    }

    expiration {
      days = 90
    }
  }
}
