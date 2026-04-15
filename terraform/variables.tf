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

variable "minio_url" {
  description = "MinIO endpoint URL"
  type        = string
  default     = "https://minioapi.digeni.vn"
}

variable "minio_access_key" {
  description = "MinIO access key"
  type        = string
  sensitive   = true
}

variable "minio_secret_key" {
  description = "MinIO secret key"
  type        = string
  sensitive   = true
}

variable "minio_bucket" {
  description = "MinIO bucket name for results and checkpoints"
  type        = string
  default     = "mistral-ai"
}

variable "http_api_base_url" {
  description = "Public base URL of the API (used in status_url field of job responses)"
  type        = string
  default     = "https://ocr-ai.digeni.vn"
}

variable "api_token" {
  description = "Bearer token required for /process and /jobs endpoints (leave empty to disable auth)"
  type        = string
  sensitive   = true
  default     = ""
}
