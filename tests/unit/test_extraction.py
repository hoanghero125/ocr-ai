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


# ── ExtractionStage ───────────────────────────────────────────────────────────

def _make_chat_response(fields: dict) -> MagicMock:
    """Build a mock chat response with given field values."""
    msg = MagicMock()
    msg.content = json.dumps(fields)
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


def _make_stage(mock_client: MagicMock) -> ExtractionStage:
    return ExtractionStage(
        client=mock_client,
        max_concurrent_pages=4,
        max_retries_per_page=2,
    )


def _make_page(page_number: int = 1, markdown: str = "Sample text") -> PageResult:
    return PageResult(page_number=page_number, markdown=markdown)


@pytest.mark.asyncio
async def test_min_confidence_below_threshold_sets_value_to_null():
    client = MagicMock()
    client.chat = AsyncMock(return_value=_make_chat_response({
        "name": {"value": "Alice", "confidence": 0.3}
    }))
    stage = _make_stage(client)
    fi = (FieldInstruction(key="name", label="Name", min_confidence=0.5),)

    pages = await stage.run([_make_page()], fi)

    assert pages[0].fields[0].value is None
    assert pages[0].fields[0].confidence == pytest.approx(0.3)


@pytest.mark.asyncio
async def test_min_confidence_above_threshold_keeps_value():
    client = MagicMock()
    client.chat = AsyncMock(return_value=_make_chat_response({
        "name": {"value": "Alice", "confidence": 0.9}
    }))
    stage = _make_stage(client)
    fi = (FieldInstruction(key="name", label="Name", min_confidence=0.5),)

    pages = await stage.run([_make_page()], fi)

    assert pages[0].fields[0].value == "Alice"


@pytest.mark.asyncio
async def test_field_missing_from_response_defaults_to_null_and_zero():
    client = MagicMock()
    client.chat = AsyncMock(return_value=_make_chat_response({}))
    stage = _make_stage(client)
    fi = (FieldInstruction(key="name", label="Name"),)

    pages = await stage.run([_make_page()], fi)

    assert pages[0].fields[0].value is None
    assert pages[0].fields[0].confidence == 0.0


@pytest.mark.asyncio
async def test_per_page_retry_fires_on_transient_error():
    client = MagicMock()
    good_response = _make_chat_response({"name": {"value": "Bob", "confidence": 0.8}})
    client.chat = AsyncMock(
        side_effect=[MistralAPIError("429", status_code=429), good_response]
    )
    stage = _make_stage(client)
    fi = (FieldInstruction(key="name", label="Name"),)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        pages = await stage.run([_make_page()], fi)

    assert client.chat.call_count == 2
    assert pages[0].fields[0].value == "Bob"


@pytest.mark.asyncio
async def test_page_error_after_all_retries_returns_error_not_raise():
    client = MagicMock()
    client.chat = AsyncMock(side_effect=MistralAPIError("500", status_code=500))
    stage = _make_stage(client)
    fi = (FieldInstruction(key="name", label="Name"),)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        pages = await stage.run([_make_page()], fi)

    assert pages[0].error is not None
    assert pages[0].fields == []


@pytest.mark.asyncio
async def test_on_page_done_callback_called():
    client = MagicMock()
    client.chat = AsyncMock(return_value=_make_chat_response(
        {"name": {"value": "X", "confidence": 1.0}}
    ))
    stage = _make_stage(client)
    fi = (FieldInstruction(key="name", label="Name"),)

    calls = []
    await stage.run([_make_page()], fi, on_page_done=lambda pn, t: calls.append((pn, t)))

    assert len(calls) == 1
    assert calls[0] == (1, 1)
