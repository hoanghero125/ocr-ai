# ── API Lambda ────────────────────────────────────────────────────────────────

resource "aws_lambda_function" "api" {
  function_name = "${local.prefix}-api"
  role          = aws_iam_role.api.arn
  package_type  = "Image"
  image_uri     = var.ecr_image_uri
  timeout       = 60
  memory_size   = 512

  image_config {
    command = ["src.lambda_handler.api_gateway_handler"]
  }

  environment {
    variables = {
      ENVIRONMENT       = var.environment
      AWS_REGION        = var.aws_region
      DYNAMODB_TABLE    = aws_dynamodb_table.jobs.name
      OCR_JOB_QUEUE_URL = aws_sqs_queue.jobs.url
      HTTP_API_BASE_URL = var.http_api_base_url
      MINIO_URL         = var.minio_url
      MINIO_ACCESS_KEY  = var.minio_access_key
      MINIO_SECRET_KEY  = var.minio_secret_key
      MINIO_BUCKET      = var.minio_bucket
    }
  }

  tags = local.common_tags
}

# ── Worker Lambda ─────────────────────────────────────────────────────────────

resource "aws_lambda_function" "worker" {
  function_name = "${local.prefix}-worker"
  role          = aws_iam_role.worker.arn
  package_type  = "Image"
  image_uri     = var.ecr_image_uri
  timeout       = 900
  memory_size   = 2048

  image_config {
    command = ["src.lambda_handler.worker_handler"]
  }

  environment {
    variables = {
      ENVIRONMENT              = var.environment
      AWS_REGION               = var.aws_region
      DYNAMODB_TABLE           = aws_dynamodb_table.jobs.name
      MISTRAL_API_KEY          = var.mistral_api_key
      MISTRAL_RATE_LIMIT_TABLE = aws_dynamodb_table.rate_limit.name
      WORKER_FUNCTION_NAME     = "${local.prefix}-worker"
      MINIO_URL                = var.minio_url
      MINIO_ACCESS_KEY         = var.minio_access_key
      MINIO_SECRET_KEY         = var.minio_secret_key
      MINIO_BUCKET             = var.minio_bucket
    }
  }

  tags = local.common_tags
}

# ── CloudWatch log groups ─────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${aws_lambda_function.api.function_name}"
  retention_in_days = 30
  tags              = local.common_tags
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/aws/lambda/${aws_lambda_function.worker.function_name}"
  retention_in_days = 30
  tags              = local.common_tags
}
