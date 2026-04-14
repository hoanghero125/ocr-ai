"""All service configuration loaded from environment variables. Read once per cold start."""

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class MistralSettings:
    api_key: str
    ocr_model: str
    chat_model: str
    table_format: str
    base_url: str
    timeout_s: int
    max_retries: int


@dataclass(frozen=True)
class AWSSettings:
    region: str
    dynamodb_table: str
    s3_results_bucket: str
    sqs_queue_url: str
    results_base_url: str
    http_api_base_url: str
    environment: str


@dataclass(frozen=True)
class RateLimitSettings:
    mistral_rps: int
    rate_limit_table: str
    rate_limit_pk: str
    rate_limit_ttl_seconds: int
    rate_limit_max_wait_seconds: int


@dataclass(frozen=True)
class ProcessingSettings:
    max_concurrent_pages: int
    lambda_time_buffer_ms: int
    lambda_extract_continuation_enabled: bool
    extract_max_retries_per_page: int
    webhook_timeout_s: int
    webhook_max_retries: int
    max_continuations: int


@dataclass(frozen=True)
class Settings:
    mistral: MistralSettings
    aws: AWSSettings
    rate_limit: RateLimitSettings
    processing: ProcessingSettings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        mistral=MistralSettings(
            api_key=os.environ["MISTRAL_API_KEY"],
            ocr_model=os.environ.get("MISTRAL_OCR_MODEL", "mistral-ocr-latest"),
            chat_model=os.environ.get("MISTRAL_CHAT_MODEL", "mistral-small-latest"),
            table_format=os.environ.get("MISTRAL_TABLE_FORMAT", "html"),
            base_url=os.environ.get("MISTRAL_BASE_URL", "https://api.mistral.ai"),
            timeout_s=int(os.environ.get("MISTRAL_TIMEOUT_S", "120")),
            max_retries=int(os.environ.get("MISTRAL_MAX_RETRIES", "4")),
        ),
        aws=AWSSettings(
            region=os.environ.get("AWS_REGION", "us-east-1"),
            dynamodb_table=os.environ["DYNAMODB_TABLE"],
            s3_results_bucket=os.environ["S3_BUCKET"],
            sqs_queue_url=os.environ.get("OCR_JOB_QUEUE_URL", ""),
            results_base_url=os.environ.get("RESULTS_BASE_URL", ""),
            http_api_base_url=os.environ.get("HTTP_API_BASE_URL", ""),
            environment=os.environ.get("ENVIRONMENT", "local"),
        ),
        rate_limit=RateLimitSettings(
            mistral_rps=int(os.environ.get("MISTRAL_RPS", "6")),
            rate_limit_table=os.environ.get("MISTRAL_RATE_LIMIT_TABLE", ""),
            rate_limit_pk=os.environ.get("MISTRAL_RATE_LIMIT_PK", "mistral"),
            rate_limit_ttl_seconds=int(os.environ.get("MISTRAL_RATE_LIMIT_TTL_SECONDS", "120")),
            rate_limit_max_wait_seconds=int(os.environ.get("MISTRAL_RATE_LIMIT_MAX_WAIT_SECONDS", "900")),
        ),
        processing=ProcessingSettings(
            max_concurrent_pages=int(os.environ.get("MAX_CONCURRENT_PAGES", "4")),
            lambda_time_buffer_ms=int(os.environ.get("LAMBDA_TIME_BUFFER_MS", "120000")),
            lambda_extract_continuation_enabled=os.environ.get(
                "LAMBDA_EXTRACT_CONTINUATION_ENABLED", "true"
            ).lower() == "true",
            extract_max_retries_per_page=int(os.environ.get("EXTRACT_MAX_RETRIES_PER_PAGE", "2")),
            webhook_timeout_s=int(os.environ.get("WEBHOOK_TIMEOUT_S", "10")),
            webhook_max_retries=int(os.environ.get("WEBHOOK_MAX_RETRIES", "3")),
            max_continuations=int(os.environ.get("MAX_CONTINUATIONS", "5")),
        ),
    )
