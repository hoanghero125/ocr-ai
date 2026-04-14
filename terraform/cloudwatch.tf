resource "aws_cloudwatch_metric_alarm" "processor_failed_errors" {
  alarm_name          = "${local.prefix}-processor-failed-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 0
  period              = 300
  statistic           = "Sum"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"

  dimensions = {
    FunctionName = aws_lambda_function.worker.function_name
  }

  alarm_description = "Worker Lambda errors — processor_failed events"
  treat_missing_data = "notBreaching"
  tags               = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "sqs_queue_age" {
  alarm_name          = "${local.prefix}-sqs-queue-age"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 3600 # 1 hour in seconds
  period              = 300
  statistic           = "Maximum"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateAgeOfOldestMessage"

  dimensions = {
    QueueName = aws_sqs_queue.jobs.name
  }

  alarm_description  = "Oldest SQS message older than 1 hour — workers may be stuck"
  treat_missing_data = "notBreaching"
  tags               = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "sqs_dlq_messages" {
  alarm_name          = "${local.prefix}-sqs-dlq-messages"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 0
  period              = 300
  statistic           = "Sum"
  namespace           = "AWS/SQS"
  metric_name         = "NumberOfMessagesSent"

  dimensions = {
    QueueName = aws_sqs_queue.jobs_dlq.name
  }

  alarm_description  = "Messages arriving on DLQ — jobs exhausted all retries"
  treat_missing_data = "notBreaching"
  tags               = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "mistral_api_errors" {
  alarm_name          = "${local.prefix}-mistral-api-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 5
  period              = 300
  statistic           = "Sum"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"

  dimensions = {
    FunctionName = aws_lambda_function.worker.function_name
  }

  alarm_description  = "High error rate on worker — possible Mistral API issues"
  treat_missing_data = "notBreaching"
  tags               = local.common_tags
}
