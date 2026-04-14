"""Unit tests for MistralClient SDK wrapper."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mistral.client import MistralClient, _is_http
from src.shared.exceptions import MistralAPIError


def _make_client(max_retries: int = 3, rate_limiter=None) -> MistralClient:
    with patch("src.mistral.client.Mistral"):
        return MistralClient(
            api_key="test-key",
            ocr_model="mistral-ocr-latest",
            chat_model="mistral-small-latest",
            table_format="html",
            base_url="https://api.mistral.ai",
            timeout_s=10,
            max_retries=max_retries,
            rate_limiter=rate_limiter,
        )


# ── _is_http ──────────────────────────────────────────────────────────────────

def test_is_http_true_for_http_url():
    assert _is_http("http://example.com/file.pdf") is True


def test_is_http_false_for_https_url():
    assert _is_http("https://example.com/file.pdf") is False


def test_is_http_false_for_data_uri():
    assert _is_http("data:application/pdf;base64,abc") is False


# ── ocr ───────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ocr_returns_sdk_response():
    client = _make_client()
    mock_response = MagicMock()
    mock_response.pages = []
    with patch("asyncio.to_thread", new_callable=AsyncMock, return_value=mock_response):
        result = await client.ocr("https://example.com/doc.pdf")
    assert result is mock_response


@pytest.mark.asyncio
async def test_ocr_downloads_http_url_first():
    client = _make_client()
    mock_response = MagicMock()
    mock_response.pages = []
    with patch("src.mistral.client._to_data_uri", new_callable=AsyncMock, return_value="data:application/pdf;base64,abc") as mock_dl:
        with patch("asyncio.to_thread", new_callable=AsyncMock, return_value=mock_response):
            await client.ocr("http://internal.example.com/doc.pdf")
    mock_dl.assert_called_once()


@pytest.mark.asyncio
async def test_ocr_passes_https_url_directly():
    client = _make_client()
    mock_response = MagicMock()
    mock_response.pages = []
    with patch("src.mistral.client._to_data_uri", new_callable=AsyncMock) as mock_dl:
        with patch("asyncio.to_thread", new_callable=AsyncMock, return_value=mock_response):
            await client.ocr("https://example.com/doc.pdf")
    mock_dl.assert_not_called()


# ── chat ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_returns_sdk_response():
    client = _make_client()
    mock_response = MagicMock()
    with patch("asyncio.to_thread", new_callable=AsyncMock, return_value=mock_response):
        result = await client.chat([{"role": "user", "content": "hello"}])
    assert result is mock_response


# ── retry logic ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retries_on_500():
    client = _make_client(max_retries=2)
    good_response = MagicMock()
    error = Exception("Server Error")
    error.status_code = 500

    with patch("asyncio.to_thread", new_callable=AsyncMock, side_effect=[error, good_response]):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client.ocr("https://example.com/doc.pdf")
    assert result is good_response


@pytest.mark.asyncio
async def test_retries_on_429():
    client = _make_client(max_retries=2)
    good_response = MagicMock()
    error = Exception("Too Many Requests")
    error.status_code = 429

    with patch("asyncio.to_thread", new_callable=AsyncMock, side_effect=[error, good_response]):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client.ocr("https://example.com/doc.pdf")
    assert result is good_response


@pytest.mark.asyncio
async def test_no_retry_on_400():
    client = _make_client(max_retries=3)
    error = Exception("Bad Request")
    error.status_code = 400

    with patch("asyncio.to_thread", new_callable=AsyncMock, side_effect=error):
        with pytest.raises(MistralAPIError) as exc_info:
            await client.ocr("https://example.com/doc.pdf")
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_all_retries_exhausted_raises_mistral_api_error():
    client = _make_client(max_retries=2)
    error = Exception("Server Error")
    error.status_code = 500

    with patch("asyncio.to_thread", new_callable=AsyncMock, side_effect=error):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(MistralAPIError):
                await client.ocr("https://example.com/doc.pdf")


# ── rate limiter ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rate_limiter_acquire_called_before_ocr():
    rate_limiter = MagicMock()
    rate_limiter.acquire = AsyncMock()
    client = _make_client(rate_limiter=rate_limiter)
    mock_response = MagicMock()
    mock_response.pages = []
    with patch("asyncio.to_thread", new_callable=AsyncMock, return_value=mock_response):
        await client.ocr("https://example.com/doc.pdf")
    rate_limiter.acquire.assert_called_once()


@pytest.mark.asyncio
async def test_rate_limiter_acquire_called_before_chat():
    rate_limiter = MagicMock()
    rate_limiter.acquire = AsyncMock()
    client = _make_client(rate_limiter=rate_limiter)
    with patch("asyncio.to_thread", new_callable=AsyncMock, return_value=MagicMock()):
        await client.chat([{"role": "user", "content": "hi"}])
    rate_limiter.acquire.assert_called_once()


@pytest.mark.asyncio
async def test_no_rate_limiter_does_not_raise():
    client = _make_client(rate_limiter=None)
    with patch("asyncio.to_thread", new_callable=AsyncMock, return_value=MagicMock()):
        await client.chat([{"role": "user", "content": "hi"}])
