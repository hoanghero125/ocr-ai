"""Unit tests for OCRProcessor pipeline orchestration."""

import dataclasses
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from src.models.job import FieldInstruction, JobPayload, JobStatus
from src.models.result import ExtractedField, JobProgress, OCRResult, PageResult
from src.pipeline.processor import OCRProcessor
from src.shared.config import AWSSettings, MistralSettings, ProcessingSettings, RateLimitSettings, Settings
from src.shared.exceptions import CheckpointError


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_settings(continuation_enabled: bool = True) -> Settings:
    return Settings(
        mistral=MistralSettings(
            api_key="k", ocr_model="m", chat_model="m",
            table_format="html", base_url="u", timeout_s=10, max_retries=1,
        ),
        aws=AWSSettings(
            region="us-east-1", dynamodb_table="t", s3_results_bucket="b",
            sqs_queue_url="q", results_base_url="", http_api_base_url="", environment="local",
        ),
        rate_limit=RateLimitSettings(
            mistral_rps=6, rate_limit_table="", rate_limit_pk="mistral",
            rate_limit_ttl_seconds=120, rate_limit_max_wait_seconds=900,
        ),
        processing=ProcessingSettings(
            max_concurrent_pages=4,
            lambda_time_buffer_ms=120_000,
            lambda_extract_continuation_enabled=continuation_enabled,
            extract_max_retries_per_page=2,
            webhook_timeout_s=10,
            webhook_max_retries=3,
            max_continuations=5,
        ),
    )


def _make_pages(n: int = 2) -> list[PageResult]:
    return [PageResult(page_number=i + 1, markdown=f"Page {i + 1}") for i in range(n)]


def _make_payload(**kwargs) -> JobPayload:
    defaults = dict(
        job_id="job-1",
        pdf_url="https://example.com/doc.pdf",
        callback_url=None,
        field_instructions=(),
        options={},
        metadata={},
    )
    defaults.update(kwargs)
    return JobPayload(**defaults)


def _make_processor(
    pages: list[PageResult] | None = None,
    continuation_enabled: bool = True,
    remaining_ms: int = 999_999,
) -> tuple[OCRProcessor, MagicMock, MagicMock, MagicMock, MagicMock, MagicMock]:
    ocr_stage = MagicMock()
    ocr_stage.run = AsyncMock(return_value=pages or _make_pages())

    extraction_stage = MagicMock()
    extraction_stage.run = AsyncMock(side_effect=lambda pages, **kw: pages)

    checkpoint = MagicMock()
    checkpoint.save_after_ocr = AsyncMock(side_effect=lambda jid, pgs, pl: dataclasses.replace(
        pl, ocr_checkpoint_key="checkpoints/job-1/ocr.json", continuation_count=pl.continuation_count + 1
    ))
    checkpoint.save_after_extraction = AsyncMock(side_effect=lambda jid, pgs, pl: dataclasses.replace(
        pl, extraction_checkpoint_key="checkpoints/job-1/extraction.json", continuation_count=pl.continuation_count + 1
    ))
    checkpoint.load_ocr_checkpoint = MagicMock(return_value=pages or _make_pages())
    checkpoint.load_extraction_checkpoint = MagicMock(return_value=pages or _make_pages())

    repo = MagicMock()
    store = MagicMock()
    store.put_result = MagicMock(return_value="s3://bucket/results/job-1/result.json")

    webhook = MagicMock()
    webhook.send = AsyncMock()

    continuation = MagicMock()
    continuation.invoke = AsyncMock()

    ctx = MagicMock()
    ctx.get_remaining_time_in_millis = MagicMock(return_value=remaining_ms)

    processor = OCRProcessor(
        ocr_stage=ocr_stage,
        extraction_stage=extraction_stage,
        checkpoint_manager=checkpoint,
        repo=repo,
        store=store,
        webhook=webhook,
        continuation=continuation,
        settings=_make_settings(continuation_enabled),
    )
    return processor, ocr_stage, extraction_stage, checkpoint, repo, continuation


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ocr_checkpoint_hit_skips_stage1():
    pages = _make_pages()
    processor, ocr_stage, _, checkpoint, repo, _ = _make_processor(pages=pages)
    payload = _make_payload(ocr_checkpoint_key="checkpoints/job-1/ocr.json")

    ctx = MagicMock()
    ctx.get_remaining_time_in_millis.return_value = 999_999

    await processor.process(payload, context=ctx)

    ocr_stage.run.assert_not_called()
    checkpoint.load_ocr_checkpoint.assert_called_once_with("checkpoints/job-1/ocr.json")


@pytest.mark.asyncio
async def test_timeout_after_ocr_triggers_continuation():
    processor, _, _, checkpoint, _, continuation = _make_processor(
        continuation_enabled=True,
        remaining_ms=50_000,  # below 120_000ms buffer
    )
    payload = _make_payload()

    ctx = MagicMock()
    ctx.get_remaining_time_in_millis.return_value = 50_000

    await processor.process(payload, context=ctx)

    checkpoint.save_after_ocr.assert_called_once()
    continuation.invoke.assert_called_once()


@pytest.mark.asyncio
async def test_page_error_does_not_abort_job():
    error_pages = [
        PageResult(page_number=1, markdown="ok"),
        PageResult(page_number=2, markdown="fail", error="extraction failed"),
    ]
    processor, _, _, _, repo, _ = _make_processor(pages=error_pages)
    payload = _make_payload()

    ctx = MagicMock()
    ctx.get_remaining_time_in_millis.return_value = 999_999

    await processor.process(payload, context=ctx)

    # Job should complete with errors, not FAILED
    status_call = repo.update_status.call_args_list[-1]
    assert status_call.args[1] == JobStatus.COMPLETED_WITH_ERRORS


@pytest.mark.asyncio
async def test_webhook_failure_does_not_fail_job():
    processor, _, _, _, repo, _ = _make_processor()
    processor._webhook.send = AsyncMock(side_effect=Exception("webhook down"))
    payload = _make_payload(callback_url="https://example.com/callback")

    ctx = MagicMock()
    ctx.get_remaining_time_in_millis.return_value = 999_999

    # Should not raise even though webhook failed
    await processor.process(payload, context=ctx)

    final_status = repo.update_status.call_args_list[-1].args[1]
    assert final_status in (JobStatus.COMPLETED, JobStatus.COMPLETED_WITH_ERRORS)


@pytest.mark.asyncio
async def test_continuation_disabled_no_checkpoint():
    processor, _, _, checkpoint, _, continuation = _make_processor(
        continuation_enabled=False,
        remaining_ms=50_000,
    )
    payload = _make_payload()
    ctx = MagicMock()
    ctx.get_remaining_time_in_millis.return_value = 50_000

    await processor.process(payload, context=ctx)

    checkpoint.save_after_ocr.assert_not_called()
    continuation.invoke.assert_not_called()


@pytest.mark.asyncio
async def test_progress_updated_after_ocr():
    processor, _, _, _, repo, _ = _make_processor(pages=_make_pages(3))
    payload = _make_payload()
    ctx = MagicMock()
    ctx.get_remaining_time_in_millis.return_value = 999_999

    await processor.process(payload, context=ctx)

    update_progress_calls = repo.update_progress.call_args_list
    assert len(update_progress_calls) >= 1
    first_progress: JobProgress = update_progress_calls[0].args[1]
    assert first_progress.total_pages == 3


@pytest.mark.asyncio
async def test_result_written_to_s3_on_completion():
    processor, _, _, _, _, _ = _make_processor()
    payload = _make_payload()
    ctx = MagicMock()
    ctx.get_remaining_time_in_millis.return_value = 999_999

    await processor.process(payload, context=ctx)

    processor._store.put_result.assert_called_once()


@pytest.mark.asyncio
async def test_uncaught_exception_marks_job_failed():
    processor, ocr_stage, _, _, repo, _ = _make_processor()
    ocr_stage.run = AsyncMock(side_effect=RuntimeError("unexpected"))
    payload = _make_payload()
    ctx = MagicMock()
    ctx.get_remaining_time_in_millis.return_value = 999_999

    with pytest.raises(RuntimeError):
        await processor.process(payload, context=ctx)

    failed_call = repo.update_status.call_args_list[-1]
    assert failed_call.args[1] == JobStatus.FAILED
