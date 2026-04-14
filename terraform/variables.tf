variable "project_name" {
  description = "Project name used as prefix for all resources"
  type        = string
  default     = "bizgenie-ocr"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  validation {
    condition     = contains(["local", "staging", "production"], var.environment)
    error_message = "environment must be local, staging, or production"
  }
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "mistral_api_key" {
  description = "Mistral API key"
  type        = string
  sensitive   = true
}

variable "ecr_image_uri" {
  description = "Full ECR image URI for both Lambda functions"
  type        = string
}

variable "results_base_url" {
  description = "Base URL for result file links (e.g. CloudFront domain)"
  type        = string
  default     = ""
}
