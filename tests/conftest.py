"""Shared pytest fixtures."""

import os

import pytest

# Set required env vars before any module is imported
os.environ.setdefault("MISTRAL_API_KEY", "test-key")
os.environ.setdefault("DYNAMODB_TABLE", "test-jobs")
os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("OCR_JOB_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123/test-queue")
os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("MISTRAL_RATE_LIMIT_TABLE", "")  # disabled in tests
