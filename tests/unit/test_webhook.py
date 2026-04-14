"""Unit tests for WebhookClient retry and error handling."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.infra.webhook import WebhookClient


def _make_mock_client(status_code: int) -> MagicMock:
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


@pytest.mark.asyncio
async def test_successful_delivery():
    mock_client = _make_mock_client(200)
    with patch("src.infra.webhook.httpx.AsyncClient", return_value=mock_client):
        webhook = WebhookClient(timeout_s=5, max_retries=3)
        await webhook.send("https://example.com/callback", {"job_id": "j1"})
    mock_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_4xx_does_not_retry():
    mock_client = _make_mock_client(404)
    with patch("src.infra.webhook.httpx.AsyncClient", return_value=mock_client):
        webhook = WebhookClient(timeout_s=5, max_retries=3)
        await webhook.send("https://example.com/callback", {})
    assert mock_client.post.call_count == 1


@pytest.mark.asyncio
async def test_5xx_retries_until_success():
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(side_effect=[
        MagicMock(status_code=500),
        MagicMock(status_code=200),
    ])
    with patch("src.infra.webhook.httpx.AsyncClient", return_value=mock_client):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            webhook = WebhookClient(timeout_s=5, max_retries=3)
            await webhook.send("https://example.com/callback", {})
    assert mock_client.post.call_count == 2


@pytest.mark.asyncio
async def test_network_error_retries():
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(side_effect=[
        httpx.RequestError("connection refused"),
        MagicMock(status_code=200),
    ])
    with patch("src.infra.webhook.httpx.AsyncClient", return_value=mock_client):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            webhook = WebhookClient(timeout_s=5, max_retries=3)
            await webhook.send("https://example.com/callback", {})
    assert mock_client.post.call_count == 2


@pytest.mark.asyncio
async def test_all_retries_exhausted_does_not_raise():
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=MagicMock(status_code=500))
    with patch("src.infra.webhook.httpx.AsyncClient", return_value=mock_client):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            webhook = WebhookClient(timeout_s=5, max_retries=2)
            await webhook.send("https://example.com/callback", {})
    assert mock_client.post.call_count == 2


@pytest.mark.asyncio
async def test_send_with_job_id_does_not_raise():
    mock_client = _make_mock_client(200)
    with patch("src.infra.webhook.httpx.AsyncClient", return_value=mock_client):
        webhook = WebhookClient(timeout_s=5, max_retries=3)
        await webhook.send("https://example.com/callback", {}, job_id="job-123")
    mock_client.post.assert_called_once()
