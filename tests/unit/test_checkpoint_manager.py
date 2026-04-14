"""Unit tests for CheckpointManager save/load paths."""

from unittest.mock import MagicMock

import pytest

from src.checkpoint.manager import CheckpointManager
from src.models.job import JobPayload
from src.models.result import PageResult


def _make_payload(**kwargs) -> JobPayload:
    defaults = dict(job_id="job-1", pdf_url="https://example.com/doc.pdf", continuation_count=0)
    defaults.update(kwargs)
    return JobPayload(**defaults)


def _make_pages() -> list[PageResult]:
    return [PageResult(page_number=1, markdown="text")]


def _make_manager(write_result: bool = True) -> tuple[CheckpointManager, MagicMock, MagicMock]:
    store = MagicMock()
    repo = MagicMock()
    repo.conditional_write_checkpoint.return_value = write_result
    repo.conditional_write_extraction_checkpoint.return_value = write_result
    return CheckpointManager(store=store, repo=repo), store, repo


@pytest.mark.asyncio
async def test_save_after_ocr_calls_put_pages():
    manager, store, _ = _make_manager()
    await manager.save_after_ocr("job-1", _make_pages(), _make_payload())
    store.put_pages.assert_called_once()
    assert "ocr.json" in store.put_pages.call_args.args[0]


@pytest.mark.asyncio
async def test_save_after_ocr_calls_conditional_write():
    manager, _, repo = _make_manager()
    await manager.save_after_ocr("job-1", _make_pages(), _make_payload())
    repo.conditional_write_checkpoint.assert_called_once()
    assert repo.conditional_write_checkpoint.call_args.kwargs["idempotency_key"] == "ocr-job-1"


@pytest.mark.asyncio
async def test_save_after_ocr_returns_updated_payload():
    manager, _, _ = _make_manager()
    new_payload = await manager.save_after_ocr("job-1", _make_pages(), _make_payload(continuation_count=0))
    assert new_payload.ocr_checkpoint_key == "checkpoints/job-1/ocr.json"
    assert new_payload.continuation_count == 1


@pytest.mark.asyncio
async def test_save_after_ocr_duplicate_still_returns_updated_payload():
    manager, _, _ = _make_manager(write_result=False)
    new_payload = await manager.save_after_ocr("job-1", _make_pages(), _make_payload())
    assert new_payload.ocr_checkpoint_key is not None


@pytest.mark.asyncio
async def test_save_after_extraction_calls_put_pages():
    manager, store, _ = _make_manager()
    await manager.save_after_extraction("job-1", _make_pages(), _make_payload())
    store.put_pages.assert_called_once()
    assert "extraction.json" in store.put_pages.call_args.args[0]


@pytest.mark.asyncio
async def test_save_after_extraction_calls_conditional_write():
    manager, _, repo = _make_manager()
    await manager.save_after_extraction("job-1", _make_pages(), _make_payload(continuation_count=2))
    repo.conditional_write_extraction_checkpoint.assert_called_once()
    key = repo.conditional_write_extraction_checkpoint.call_args.kwargs["idempotency_key"]
    assert key == "extraction-job-1-002"


@pytest.mark.asyncio
async def test_save_after_extraction_returns_updated_payload():
    manager, _, _ = _make_manager()
    new_payload = await manager.save_after_extraction("job-1", _make_pages(), _make_payload(continuation_count=1))
    assert new_payload.extraction_checkpoint_key == "checkpoints/job-1/extraction.json"
    assert new_payload.continuation_count == 2


@pytest.mark.asyncio
async def test_save_after_extraction_duplicate_still_returns_updated_payload():
    manager, _, _ = _make_manager(write_result=False)
    new_payload = await manager.save_after_extraction("job-1", _make_pages(), _make_payload())
    assert new_payload.extraction_checkpoint_key is not None


def test_load_ocr_checkpoint_delegates_to_store():
    manager, store, _ = _make_manager()
    pages = _make_pages()
    store.get_pages.return_value = pages
    result = manager.load_ocr_checkpoint("checkpoints/job-1/ocr.json")
    store.get_pages.assert_called_once_with("checkpoints/job-1/ocr.json")
    assert result is pages


def test_load_extraction_checkpoint_delegates_to_store():
    manager, store, _ = _make_manager()
    pages = _make_pages()
    store.get_pages.return_value = pages
    result = manager.load_extraction_checkpoint("checkpoints/job-1/extraction.json")
    store.get_pages.assert_called_once_with("checkpoints/job-1/extraction.json")
    assert result is pages
