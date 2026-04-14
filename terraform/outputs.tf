output "api_endpoint" {
  description = "HTTP API Gateway endpoint URL"
  value       = aws_apigatewayv2_stage.default.invoke_url
}

output "jobs_table_name" {
  description = "DynamoDB jobs table name"
  value       = aws_dynamodb_table.jobs.name
}

output "rate_limit_table_name" {
  description = "DynamoDB Mistral rate limit table name"
  value       = aws_dynamodb_table.rate_limit.name
}

output "results_bucket" {
  description = "S3 results bucket name"
  value       = aws_s3_bucket.results.bucket
}

output "jobs_queue_url" {
  description = "SQS main jobs queue URL"
  value       = aws_sqs_queue.jobs.url
}

output "jobs_dlq_url" {
  description = "SQS dead-letter queue URL"
  value       = aws_sqs_queue.jobs_dlq.url
}

output "api_lambda_name" {
  description = "API Lambda function name"
  value       = aws_lambda_function.api.function_name
}

output "worker_lambda_name" {
  description = "Worker Lambda function name"
  value       = aws_lambda_function.worker.function_name
}
