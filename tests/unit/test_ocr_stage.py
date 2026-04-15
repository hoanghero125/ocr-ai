"""Unit tests for OCRStage."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.mistral.ocr import OCRStage
from src.models.result import PageResult


def _make_response(pages: list[dict]) -> MagicMock:
    mock_pages = []
    for p in pages:
        page = MagicMock()
        page.index = p["index"]
        page.markdown = p["markdown"]
        mock_pages.append(page)
    response = MagicMock()
    response.pages = mock_pages
    return response


def _make_stage() -> tuple[OCRStage, MagicMock]:
    client = MagicMock()
    return OCRStage(client=client), client


@pytest.mark.asyncio
async def test_run_returns_one_page_result_per_page():
    stage, client = _make_stage()
    client.ocr = AsyncMock(return_value=_make_response([
        {"index": 0, "markdown": "Page 1"},
        {"index": 1, "markdown": "Page 2"},
    ]))
    pages = await stage.run("https://example.com/doc.pdf")
    assert len(pages) == 2
    assert all(isinstance(p, PageResult) for p in pages)


@pytest.mark.asyncio
async def test_run_sets_page_number_from_index():
    stage, client = _make_stage()
    client.ocr = AsyncMock(return_value=_make_response([
        {"index": 0, "markdown": ""},
        {"index": 2, "markdown": ""},
    ]))
    pages = await stage.run("https://example.com/doc.pdf")
    assert pages[0].page_number == 1
    assert pages[1].page_number == 3


@pytest.mark.asyncio
async def test_run_parses_tables_from_markdown():
    stage, client = _make_stage()
    client.ocr = AsyncMock(return_value=_make_response([
        {"index": 0, "markdown": "| A | B |\n|---|---|\n| 1 | 2 |"},
    ]))
    pages = await stage.run("https://example.com/doc.pdf")
    assert len(pages[0].tables) == 1
    assert pages[0].tables[0].headers == ["A", "B"]


@pytest.mark.asyncio
async def test_run_fields_are_empty_after_stage1():
    stage, client = _make_stage()
    client.ocr = AsyncMock(return_value=_make_response([
        {"index": 0, "markdown": "some text"},
    ]))
    pages = await stage.run("https://example.com/doc.pdf")
    assert pages[0].extracted_fields == []


@pytest.mark.asyncio
async def test_run_handles_empty_response():
    stage, client = _make_stage()
    response = MagicMock()
    response.pages = []
    client.ocr = AsyncMock(return_value=response)
    pages = await stage.run("https://example.com/doc.pdf")
    assert pages == []


@pytest.mark.asyncio
async def test_run_passes_job_id_to_logger():
    stage, client = _make_stage()
    client.ocr = AsyncMock(return_value=_make_response([{"index": 0, "markdown": ""}]))
    # Should not raise with job_id provided
    await stage.run("https://example.com/doc.pdf", job_id="job-123")
