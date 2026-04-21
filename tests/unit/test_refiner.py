"""Unit tests for RefineHandler re-extraction logic."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.job import FieldInstruction
from src.models.result import ExtractedField, PageResult
from src.pipeline.refiner import RefineHandler
from src.shared.exceptions import JobNotFoundError


def _make_fi(key: str = "ten", label: str = "Ten") -> FieldInstruction:
    return FieldInstruction(key=key, label=label)


def _make_field(key: str, value: str | None, confidence: float = 0.9) -> ExtractedField:
    return ExtractedField(key=key, label=key.upper(), value=value, confidence=confidence)


def _make_handler(
    status: str = "completed",
    pages_markdown: list[str] | None = None,
    original_fields: list[dict] | None = None,
    refined_pages: list[PageResult] | None = None,
) -> tuple[RefineHandler, MagicMock, MagicMock, MagicMock]:
    repo = MagicMock()
    repo.get.return_value = {"status": status}

    store = MagicMock()
    store.get_result = AsyncMock(return_value={
        "pages_markdown": pages_markdown if pages_markdown is not None else ["page 1 text"],
        "extracted_fields": original_fields or [],
    })
    store.put_result_raw = AsyncMock()

    extraction_stage = MagicMock()
    extraction_stage.run = AsyncMock(return_value=refined_pages or [
        PageResult(
            page_number=1,
            extracted_fields=[_make_field("ten", "Nguyen Van A")],
        )
    ])

    handler = RefineHandler(store=store, extraction_stage=extraction_stage, repo=repo)
    return handler, store, extraction_stage, repo


# ── Happy path ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_refine_returns_refined_fields():
    handler, _, _, _ = _make_handler()
    result = await handler.refine("job-1", (_make_fi("ten", "Ten"),))
    assert len(result["refined_fields"]) == 1
    assert result["refined_fields"][0]["key"] == "ten"
    assert result["refined_fields"][0]["value"] == "Nguyen Van A"


@pytest.mark.asyncio
async def test_refine_overwrites_null_original_field():
    original = [{"key": "ten", "label": "TEN", "value": None, "confidence": 0.3, "field_type": "typed"}]
    handler, store, _, _ = _make_handler(original_fields=original)
    await handler.refine("job-1", (_make_fi("ten"),))

    saved = store.put_result_raw.call_args.args[1]
    merged = {f["key"]: f for f in saved["extracted_fields"]}
    assert merged["ten"]["value"] == "Nguyen Van A"


@pytest.mark.asyncio
async def test_refine_keeps_untouched_original_fields():
    original = [
        {"key": "ten", "label": "TEN", "value": None, "confidence": 0.3, "field_type": "typed"},
        {"key": "dia_chi", "label": "DIA_CHI", "value": "Ha Noi", "confidence": 0.95, "field_type": "typed"},
    ]
    handler, store, _, _ = _make_handler(original_fields=original)
    await handler.refine("job-1", (_make_fi("ten"),))

    saved = store.put_result_raw.call_args.args[1]
    merged = {f["key"]: f for f in saved["extracted_fields"]}
    assert merged["dia_chi"]["value"] == "Ha Noi"


@pytest.mark.asyncio
async def test_refine_appends_new_key_not_in_original():
    handler, store, _, _ = _make_handler(original_fields=[])
    await handler.refine("job-1", (_make_fi("ten"),))

    saved = store.put_result_raw.call_args.args[1]
    assert any(f["key"] == "ten" for f in saved["extracted_fields"])


@pytest.mark.asyncio
async def test_refine_saves_updated_result_to_store():
    handler, store, _, _ = _make_handler()
    await handler.refine("job-1", (_make_fi("ten"),))
    store.put_result_raw.assert_called_once_with("job-1", store.put_result_raw.call_args.args[1])


@pytest.mark.asyncio
async def test_refine_passes_field_instructions_to_extraction():
    handler, _, extraction_stage, _ = _make_handler()
    fi = _make_fi("ten", "Ten")
    await handler.refine("job-1", (fi,))
    called_fi = extraction_stage.run.call_args.kwargs.get("field_instructions") \
        or extraction_stage.run.call_args.args[1]
    assert fi in called_fi


@pytest.mark.asyncio
async def test_refine_reports_pages_reprocessed():
    handler, _, _, _ = _make_handler(pages_markdown=["p1", "p2", "p3"])
    result = await handler.refine("job-1", (_make_fi("ten"),))
    assert result["pages_reprocessed"] == 3


# ── Error cases ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_refine_raises_job_not_found():
    handler, _, _, repo = _make_handler()
    repo.get.side_effect = JobNotFoundError("job-999")
    with pytest.raises(JobNotFoundError):
        await handler.refine("job-999", (_make_fi("ten"),))


@pytest.mark.asyncio
async def test_refine_raises_value_error_if_job_not_completed():
    handler, _, _, _ = _make_handler(status="processing")
    with pytest.raises(ValueError, match="cannot be refined"):
        await handler.refine("job-1", (_make_fi("ten"),))


@pytest.mark.asyncio
async def test_refine_raises_value_error_if_no_pages_markdown():
    handler, _, _, _ = _make_handler(pages_markdown=[])
    with pytest.raises(ValueError, match="No OCR text stored"):
        await handler.refine("job-1", (_make_fi("ten"),))
