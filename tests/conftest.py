"""Shared pytest fixtures."""

import os

import pytest

# Set required env vars before any module is imported
os.environ.setdefault("MISTRAL_API_KEY", "test-key")
os.environ.setdefault("DYNAMODB_TABLE", "test-jobs")
os.environ.setdefault("OCR_JOB_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123/test-queue")
os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("MISTRAL_RATE_LIMIT_TABLE", "")  # disabled in tests
os.environ.setdefault("MINIO_URL", "http://localhost:9000")
os.environ.setdefault("MINIO_ACCESS_KEY", "test-access")
os.environ.setdefault("MINIO_SECRET_KEY", "test-secret")
os.environ.setdefault("MINIO_BUCKET", "test-bucket")
