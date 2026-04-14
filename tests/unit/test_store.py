"""Unit tests for ResultStore S3 serialization round-trip."""

import io
from unittest.mock import MagicMock

import pytest

from src.infra.store import ResultStore
from src.models.result import ExtractedField, ExtractedTable, OCRResult, PageResult


def _make_store() -> tuple[ResultStore, MagicMock]:
    s3 = MagicMock()
    return ResultStore(s3_client=s3, bucket="test-bucket"), s3


def _make_result() -> OCRResult:
    return OCRResult(
        job_id="job-1",
        status="completed",
        pages=[PageResult(page_number=1, markdown="text")],
        total_pages=1,
        processed_pages=1,
        errors=[],
        metadata={},
    )


def _round_trip_store() -> tuple[ResultStore, list]:
    """Return a store that captures put_object body and serves it back via get_object."""
    s3 = MagicMock()
    captured: list = []

    def capture_put(**kwargs):
        captured.append(kwargs["Body"])

    def serve_get(**kwargs):
        return {"Body": io.BytesIO(captured[-1])}

    s3.put_object = MagicMock(side_effect=capture_put)
    s3.get_object = MagicMock(side_effect=serve_get)
    return ResultStore(s3_client=s3, bucket="test-bucket"), captured


# ── put_result ────────────────────────────────────────────────────────────────

def test_put_result_calls_put_object():
    store, s3 = _make_store()
    store.put_result("job-1", _make_result())
    s3.put_object.assert_called_once()


def test_put_result_uses_correct_key():
    store, s3 = _make_store()
    store.put_result("job-1", _make_result())
    kwargs = s3.put_object.call_args.kwargs
    assert kwargs["Key"] == "results/job-1/result.json"
    assert kwargs["Bucket"] == "test-bucket"


def test_put_result_returns_s3_uri():
    store, s3 = _make_store()
    uri = store.put_result("job-1", _make_result())
    assert uri == "s3://test-bucket/results/job-1/result.json"


# ── put_pages ─────────────────────────────────────────────────────────────────

def test_put_pages_calls_put_object_with_correct_key():
    store, s3 = _make_store()
    store.put_pages("checkpoints/job-1/ocr.json", [PageResult(page_number=1, markdown="hi")])
    s3.put_object.assert_called_once()
    assert s3.put_object.call_args.kwargs["Key"] == "checkpoints/job-1/ocr.json"


# ── get_pages round-trip ──────────────────────────────────────────────────────

def test_get_pages_returns_typed_page_results():
    store, _ = _round_trip_store()
    pages = [
        PageResult(
            page_number=1,
            markdown="hello",
            tables=[ExtractedTable(headers=["A", "B"], rows=[["1", "2"]], raw="<table>")],
            fields=[ExtractedField(key="name", label="Name", value="Alice", confidence=0.9)],
        )
    ]
    store.put_pages("test/key.json", pages)
    result = store.get_pages("test/key.json")

    assert isinstance(result[0], PageResult)
    assert isinstance(result[0].tables[0], ExtractedTable)
    assert isinstance(result[0].fields[0], ExtractedField)
    assert result[0].fields[0].value == "Alice"
    assert result[0].tables[0].headers == ["A", "B"]
    assert result[0].tables[0].rows == [["1", "2"]]


def test_get_pages_preserves_page_error():
    store, _ = _round_trip_store()
    pages = [PageResult(page_number=1, markdown="text", error="extraction failed")]
    store.put_pages("test/key.json", pages)
    result = store.get_pages("test/key.json")
    assert result[0].error == "extraction failed"


def test_get_pages_handles_empty_tables_and_fields():
    store, _ = _round_trip_store()
    pages = [PageResult(page_number=1, markdown="text")]
    store.put_pages("test/key.json", pages)
    result = store.get_pages("test/key.json")
    assert result[0].tables == []
    assert result[0].fields == []
