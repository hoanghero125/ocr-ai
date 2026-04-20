"""Unit tests for ExtractionStage and sanitize_label."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mistral.extraction import ExtractionStage, sanitize_label
from src.models.job import FieldInstruction
from src.models.result import PageResult
from src.shared.exceptions import MistralAPIError


# ── sanitize_label ────────────────────────────────────────────────────────────

def test_sanitize_label_strips_newline():
    assert "\n" not in sanitize_label("Full\nName")


def test_sanitize_label_strips_carriage_return():
    assert "\r" not in sanitize_label("Full\rName")


def test_sanitize_label_strips_null_byte():
    assert "\x00" not in sanitize_label("Name\x00Injected")


def test_sanitize_label_strips_control_chars():
    result = sanitize_label("Hello\x01\x1fWorld")
    assert "\x01" not in result
    assert "\x1f" not in result
    assert "HelloWorld" in result


def test_sanitize_label_truncates_at_200():
    long_label = "a" * 300
    assert len(sanitize_label(long_label)) == 200


def test_sanitize_label_preserves_normal_text():
    assert sanitize_label("Full Name") == "Full Name"


# ── ExtractionStage helpers ───────────────────────────────────────────────────

def _make_chat_response(payload: dict) -> MagicMock:
    """Build a mock chat response with the new extraction format."""
    msg = MagicMock()
    msg.content = json.dumps(payload)
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


def _extraction_response(extracted: list = None, free_texts: list = None,
                          confidence: float = 0.9, handwritten_pct: int = 0) -> dict:
    return {
        "extracted_fields": extracted or [],
        "free_texts": free_texts or [],
        "confidence": confidence,
        "handwritten_percentage": handwritten_pct,
    }


def _make_stage(mock_client: MagicMock) -> ExtractionStage:
    return ExtractionStage(client=mock_client, max_concurrent_pages=4, max_retries_per_page=2)


def _make_page(page_number: int = 1, markdown: str = "Sample text") -> PageResult:
    return PageResult(page_number=page_number, markdown=markdown)


# ── min_confidence ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_min_confidence_below_threshold_sets_value_to_null():
    client = MagicMock()
    client.chat = AsyncMock(return_value=_make_chat_response(_extraction_response(
        extracted=[{"key": "name", "label": "Name", "value": "Alice", "confidence": 0.3, "field_type": "typed"}]
    )))
    stage = _make_stage(client)
    fi = (FieldInstruction(key="name", label="Name", min_confidence=0.5),)

    pages = await stage.run([_make_page()], fi)

    assert pages[0].extracted_fields[0].value is None
    assert pages[0].extracted_fields[0].confidence == pytest.approx(0.3)


@pytest.mark.asyncio
async def test_min_confidence_above_threshold_keeps_value():
    client = MagicMock()
    client.chat = AsyncMock(return_value=_make_chat_response(_extraction_response(
        extracted=[{"key": "name", "label": "Name", "value": "Alice", "confidence": 0.9, "field_type": "typed"}]
    )))
    stage = _make_stage(client)
    fi = (FieldInstruction(key="name", label="Name", min_confidence=0.5),)

    pages = await stage.run([_make_page()], fi)

    assert pages[0].extracted_fields[0].value == "Alice"


@pytest.mark.asyncio
async def test_field_missing_from_response_returns_empty_extracted():
    client = MagicMock()
    client.chat = AsyncMock(return_value=_make_chat_response(_extraction_response()))
    stage = _make_stage(client)
    fi = (FieldInstruction(key="name", label="Name"),)

    pages = await stage.run([_make_page()], fi)

    assert pages[0].extracted_fields == []


# ── extracted_fields (auto mode) and free_texts ──────────────────────────────

@pytest.mark.asyncio
async def test_auto_mode_fields_are_parsed_into_extracted_fields():
    client = MagicMock()
    client.chat = AsyncMock(return_value=_make_chat_response(_extraction_response(
        extracted=[{"key": "ngay_thang", "label": "Ngày tháng", "value": "01/01/2024", "confidence": 0.95, "field_type": "typed"}]
    )))
    stage = _make_stage(client)

    pages = await stage.run([_make_page()], ())

    assert len(pages[0].extracted_fields) == 1
    assert pages[0].extracted_fields[0].key == "ngay_thang"
    assert pages[0].extracted_fields[0].value == "01/01/2024"


@pytest.mark.asyncio
async def test_free_texts_are_parsed():
    client = MagicMock()
    client.chat = AsyncMock(return_value=_make_chat_response(_extraction_response(
        free_texts=[{"content": "Paragraph text", "confidence": 0.9, "field_type": "typed", "position": "body"}]
    )))
    stage = _make_stage(client)

    pages = await stage.run([_make_page()], ())

    assert len(pages[0].free_texts) == 1
    assert pages[0].free_texts[0].content == "Paragraph text"
    assert pages[0].free_texts[0].position == "body"


@pytest.mark.asyncio
async def test_handwritten_percentage_and_confidence_are_parsed():
    client = MagicMock()
    client.chat = AsyncMock(return_value=_make_chat_response(
        _extraction_response(confidence=0.85, handwritten_pct=30)
    ))
    stage = _make_stage(client)

    pages = await stage.run([_make_page()], ())

    assert pages[0].confidence == pytest.approx(0.85)
    assert pages[0].handwritten_percentage == 30


# ── retry and error handling ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_per_page_retry_fires_on_transient_error():
    client = MagicMock()
    good_response = _make_chat_response(_extraction_response(
        extracted=[{"key": "name", "label": "Name", "value": "Bob", "confidence": 0.8, "field_type": "typed"}]
    ))
    client.chat = AsyncMock(
        side_effect=[MistralAPIError("429", status_code=429), good_response]
    )
    stage = _make_stage(client)
    fi = (FieldInstruction(key="name", label="Name"),)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        pages = await stage.run([_make_page()], fi)

    assert client.chat.call_count == 2
    assert pages[0].extracted_fields[0].value == "Bob"


@pytest.mark.asyncio
async def test_page_error_after_all_retries_returns_error_not_raise():
    client = MagicMock()
    client.chat = AsyncMock(side_effect=MistralAPIError("500", status_code=500))
    stage = _make_stage(client)
    fi = (FieldInstruction(key="name", label="Name"),)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        pages = await stage.run([_make_page()], fi)

    assert pages[0].error_message is not None
    assert pages[0].status == "error"
    assert pages[0].extracted_fields == []


@pytest.mark.asyncio
async def test_on_page_done_callback_called():
    client = MagicMock()
    client.chat = AsyncMock(return_value=_make_chat_response(_extraction_response(
        extracted=[{"key": "name", "label": "Name", "value": "X", "confidence": 1.0, "field_type": "typed"}]
    )))
    stage = _make_stage(client)
    fi = (FieldInstruction(key="name", label="Name"),)

    calls = []
    await stage.run([_make_page()], fi, on_page_done=lambda pn, t: calls.append((pn, t)))

    assert len(calls) == 1
    assert calls[0] == (1, 1)
