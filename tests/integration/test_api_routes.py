"""Integration tests for API routes using moto-mocked AWS."""

import asyncio
import dataclasses
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from src.api.routes import handle_api_event
from src.models.job import JobStatus


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def aws_credentials():
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


@pytest.fixture
def aws_resources(aws_credentials):
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamodb.create_table(
            TableName="test-jobs",
            KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "job_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        sqs = boto3.client("sqs", region_name="us-east-1")
        queue = sqs.create_queue(QueueName="test-queue")
        queue_url = queue["QueueUrl"]

        os.environ["OCR_JOB_QUEUE_URL"] = queue_url

        yield {"table": table, "sqs": sqs, "queue_url": queue_url}


def _make_container(table, queue_url: str) -> MagicMock:
    from src.infra.repository import JobRepository
    from src.shared.config import AWSSettings, MistralSettings, ProcessingSettings, RateLimitSettings, Settings

    settings = Settings(
        mistral=MistralSettings("k", "m", "m", "html", "u", 10, 1),
        aws=AWSSettings("us-east-1", "test-jobs", "test-bucket", queue_url, "", "", "local"),
        rate_limit=RateLimitSettings(6, "", "mistral", 120, 900),
        processing=ProcessingSettings(4, 120000, False, 2, 10, 3, 5),
    )
    container = MagicMock()
    container.settings = settings
    container.get_repo.return_value = JobRepository(table)
    return container


def _post_event(body: dict) -> dict:
    return {
        "httpMethod": "POST",
        "path": "/process",
        "body": json.dumps(body),
    }


def _get_event(path: str) -> dict:
    return {"httpMethod": "GET", "path": path}


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_process_creates_job_and_returns_202(aws_resources):
    container = _make_container(aws_resources["table"], aws_resources["queue_url"])

    with patch("src.api.routes.validate_url", new_callable=AsyncMock):
        with patch("boto3.client") as mock_boto_client:
            mock_sqs = MagicMock()
            mock_boto_client.return_value = mock_sqs

            response = await handle_api_event(
                _post_event({"pdf_url": "https://example.com/doc.pdf"}),
                context=MagicMock(),
                container=container,
            )

    assert response["statusCode"] == 202
    body = json.loads(response["body"])
    assert "job_id" in body
    assert body["status"] == "queued"
    assert "/jobs/" in body["status_url"]


@pytest.mark.asyncio
async def test_post_process_ssrf_blocked_returns_400(aws_resources):
    container = _make_container(aws_resources["table"], aws_resources["queue_url"])

    from src.shared.exceptions import SSRFBlockedError

    with patch("src.api.routes.validate_url", new_callable=AsyncMock, side_effect=SSRFBlockedError()):
        response = await handle_api_event(
            _post_event({"pdf_url": "https://internal.local/doc.pdf"}),
            context=MagicMock(),
            container=container,
        )

    assert response["statusCode"] == 400


@pytest.mark.asyncio
async def test_get_job_returns_status_and_progress(aws_resources):
    table = aws_resources["table"]
    container = _make_container(table, aws_resources["queue_url"])

    from datetime import datetime, timezone
    now = datetime.now(tz=timezone.utc).isoformat()
    table.put_item(Item={
        "job_id": "job-exists",
        "status": JobStatus.PROCESSING.value,
        "progress": {"total_pages": 5, "processed_pages": 2, "current_step": "Extracting"},
        "created_at": now,
        "updated_at": now,
    })

    response = await handle_api_event(
        _get_event("/jobs/job-exists"),
        context=MagicMock(),
        container=container,
    )

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["status"] == "processing"
    assert body["progress"]["total_pages"] == 5


@pytest.mark.asyncio
async def test_get_unknown_job_returns_404(aws_resources):
    container = _make_container(aws_resources["table"], aws_resources["queue_url"])

    response = await handle_api_event(
        _get_event("/jobs/does-not-exist"),
        context=MagicMock(),
        container=container,
    )

    assert response["statusCode"] == 404


@pytest.mark.asyncio
async def test_get_health_returns_200(aws_resources):
    container = _make_container(aws_resources["table"], aws_resources["queue_url"])

    response = await handle_api_event(
        _get_event("/health"),
        context=MagicMock(),
        container=container,
    )

    assert response["statusCode"] == 200
    assert json.loads(response["body"])["status"] == "healthy"


@pytest.mark.asyncio
async def test_invalid_request_body_returns_400(aws_resources):
    container = _make_container(aws_resources["table"], aws_resources["queue_url"])

    response = await handle_api_event(
        _post_event({}),  # missing required pdf_url
        context=MagicMock(),
        container=container,
    )

    assert response["statusCode"] == 400
