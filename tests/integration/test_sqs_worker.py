"""Integration tests for the SQS worker handler using moto."""

import asyncio
import dataclasses
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from src.models.job import JobPayload
from src.workers.sqs import handle_direct_invocation, handle_sqs_batch


def _make_sqs_record(job_id: str, message_id: str | None = None) -> dict:
    payload = JobPayload(
        job_id=job_id,
        pdf_url="https://example.com/doc.pdf",
    )
    return {
        "messageId": message_id or f"msg-{job_id}",
        "body": json.dumps(dataclasses.asdict(payload)),
    }


def _make_container(processor_mock: MagicMock) -> MagicMock:
    container = MagicMock()
    container.get_processor.return_value = processor_mock
    return container


def test_valid_message_processes_successfully():
    processor = MagicMock()
    processor.process = AsyncMock()
    container = _make_container(processor)

    event = {"Records": [_make_sqs_record("job-1")]}
    result = handle_sqs_batch(event, context=MagicMock(), container=container)

    assert result == {"batchItemFailures": []}
    processor.process.assert_called_once()


def test_failed_message_appears_in_batch_item_failures():
    processor = MagicMock()
    processor.process = AsyncMock(side_effect=RuntimeError("boom"))
    container = _make_container(processor)

    event = {"Records": [_make_sqs_record("job-fail", message_id="msg-fail")]}
    result = handle_sqs_batch(event, context=MagicMock(), container=container)

    assert result == {"batchItemFailures": [{"itemIdentifier": "msg-fail"}]}


def test_other_messages_still_processed_after_one_failure():
    call_count = 0

    async def process_side_effect(payload, context):
        nonlocal call_count
        call_count += 1
        if payload.job_id == "job-bad":
            raise RuntimeError("fail")

    processor = MagicMock()
    processor.process = AsyncMock(side_effect=process_side_effect)
    container = _make_container(processor)

    event = {
        "Records": [
            _make_sqs_record("job-good-1", "msg-1"),
            _make_sqs_record("job-bad", "msg-bad"),
            _make_sqs_record("job-good-2", "msg-3"),
        ]
    }
    result = handle_sqs_batch(event, context=MagicMock(), container=container)

    assert result == {"batchItemFailures": [{"itemIdentifier": "msg-bad"}]}
    assert call_count == 3  # all three records were attempted


def test_missing_body_is_reported_as_failure():
    """A record with no 'body' key must not raise — it should be a batch failure."""
    processor = MagicMock()
    processor.process = AsyncMock()
    container = _make_container(processor)

    event = {"Records": [{"messageId": "msg-nobody"}]}  # no 'body' key
    result = handle_sqs_batch(event, context=MagicMock(), container=container)

    assert result == {"batchItemFailures": [{"itemIdentifier": "msg-nobody"}]}
    processor.process.assert_not_called()


def test_continuation_count_clamped_to_max():
    """continuation_count from a corrupt/replayed message must be clamped."""
    processor = MagicMock()
    processor.process = AsyncMock()
    container = _make_container(processor)

    payload_dict = {
        "job_id": "job-clamp",
        "pdf_url": "https://example.com/doc.pdf",
        "continuation_count": 9999,
    }
    record = {"messageId": "msg-clamp", "body": json.dumps(payload_dict)}
    handle_sqs_batch({"Records": [record]}, context=MagicMock(), container=container)

    called_payload: JobPayload = processor.process.call_args.args[0]
    assert called_payload.continuation_count <= 100


def test_direct_invocation_continuation_routed_correctly():
    processor = MagicMock()
    processor.process = AsyncMock()
    container = _make_container(processor)

    payload = JobPayload(
        job_id="job-cont",
        pdf_url="https://example.com/doc.pdf",
        continuation_count=1,
        ocr_checkpoint_key="checkpoints/job-cont/ocr.json",
    )
    event = dataclasses.asdict(payload)

    handle_direct_invocation(event, context=MagicMock(), container=container)

    processor.process.assert_called_once()
    called_payload: JobPayload = processor.process.call_args.args[0]
    assert called_payload.continuation_count == 1
    assert called_payload.ocr_checkpoint_key == "checkpoints/job-cont/ocr.json"
