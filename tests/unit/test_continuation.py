"""Unit tests for ContinuationTrigger."""

import json
from unittest.mock import MagicMock

import pytest

from src.models.job import JobPayload
from src.pipeline.continuation import ContinuationTrigger
from src.shared.exceptions import CheckpointError


def _make_payload(continuation_count: int = 0) -> JobPayload:
    return JobPayload(
        job_id="job-1",
        pdf_url="https://example.com/doc.pdf",
        continuation_count=continuation_count,
    )


def _make_trigger(max_continuations: int = 5) -> tuple[ContinuationTrigger, MagicMock]:
    lambda_client = MagicMock()
    lambda_client.invoke = MagicMock(return_value={})
    trigger = ContinuationTrigger(
        lambda_client=lambda_client,
        function_name="my-worker",
        max_continuations=max_continuations,
    )
    return trigger, lambda_client


@pytest.mark.asyncio
async def test_invoke_calls_lambda_invoke():
    trigger, lambda_client = _make_trigger()
    await trigger.invoke(_make_payload(continuation_count=1))
    lambda_client.invoke.assert_called_once()


@pytest.mark.asyncio
async def test_invoke_uses_event_invocation_type():
    trigger, lambda_client = _make_trigger()
    await trigger.invoke(_make_payload(continuation_count=1))
    kwargs = lambda_client.invoke.call_args.kwargs
    assert kwargs["InvocationType"] == "Event"


@pytest.mark.asyncio
async def test_invoke_uses_correct_function_name():
    trigger, lambda_client = _make_trigger()
    await trigger.invoke(_make_payload(continuation_count=1))
    kwargs = lambda_client.invoke.call_args.kwargs
    assert kwargs["FunctionName"] == "my-worker"


@pytest.mark.asyncio
async def test_invoke_payload_is_valid_json_with_job_id():
    trigger, lambda_client = _make_trigger()
    await trigger.invoke(_make_payload(continuation_count=1))
    raw = lambda_client.invoke.call_args.kwargs["Payload"]
    parsed = json.loads(raw.decode())
    assert parsed["job_id"] == "job-1"


@pytest.mark.asyncio
async def test_max_continuations_exceeded_raises_checkpoint_error():
    trigger, _ = _make_trigger(max_continuations=3)
    with pytest.raises(CheckpointError):
        await trigger.invoke(_make_payload(continuation_count=4))


@pytest.mark.asyncio
async def test_at_exact_limit_does_not_raise():
    trigger, lambda_client = _make_trigger(max_continuations=5)
    await trigger.invoke(_make_payload(continuation_count=5))
    lambda_client.invoke.assert_called_once()


@pytest.mark.asyncio
async def test_invoke_zero_continuations_allowed():
    trigger, lambda_client = _make_trigger(max_continuations=5)
    await trigger.invoke(_make_payload(continuation_count=0))
    lambda_client.invoke.assert_called_once()
