# ── Dead-letter queue ─────────────────────────────────────────────────────────

resource "aws_sqs_queue" "jobs_dlq" {
  name                      = "${local.prefix}-jobs-dlq"
  message_retention_seconds = 1209600 # 14 days
  tags                      = local.common_tags
}

# ── Main processing queue ─────────────────────────────────────────────────────

resource "aws_sqs_queue" "jobs" {
  name                       = "${local.prefix}-jobs"
  visibility_timeout_seconds = 960 # must exceed worker Lambda timeout (900s)
  message_retention_seconds  = 345600 # 4 days

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.jobs_dlq.arn
    maxReceiveCount     = 3
  })

  tags = local.common_tags
}

# ── SQS event source mapping → worker Lambda ─────────────────────────────────

resource "aws_lambda_event_source_mapping" "sqs_to_worker" {
  event_source_arn = aws_sqs_queue.jobs.arn
  function_name    = aws_lambda_function.worker.arn
  batch_size       = 1
  enabled          = true
}
