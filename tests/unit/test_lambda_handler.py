"""Unit tests for lambda_handler routing logic."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.lambda_handler import api_gateway_handler, handler, worker_handler


@pytest.fixture(autouse=True)
def clear_container_cache():
    from src.container import get_container
    get_container.cache_clear()
    yield
    get_container.cache_clear()


def test_api_gateway_handler_returns_api_response():
    mock_response = {"statusCode": 200, "body": '{"status": "healthy"}'}
    event = {"httpMethod": "GET", "path": "/health"}

    with patch("src.lambda_handler.get_container"):
        with patch("src.api.routes.handle_api_event", new_callable=AsyncMock, return_value=mock_response):
            result = api_gateway_handler(event, MagicMock())

    assert result["statusCode"] == 200


def test_worker_handler_routes_sqs_event():
    event = {"Records": [{"messageId": "msg-1", "body": "{}"}]}

    with patch("src.lambda_handler.get_container"):
        with patch("src.lambda_handler.handle_sqs_batch", return_value={"batchItemFailures": []}) as mock_sqs:
            result = worker_handler(event, MagicMock())

    mock_sqs.assert_called_once()
    assert result == {"batchItemFailures": []}


def test_worker_handler_routes_direct_invocation():
    event = {"job_id": "job-1", "pdf_url": "https://example.com/doc.pdf", "continuation_count": 1}

    with patch("src.lambda_handler.get_container"):
        with patch("src.lambda_handler.handle_direct_invocation") as mock_direct:
            worker_handler(event, MagicMock())

    mock_direct.assert_called_once()


def test_combined_handler_routes_httpmethod_to_api():
    event = {"httpMethod": "GET", "path": "/health"}

    with patch("src.lambda_handler.api_gateway_handler", return_value={"statusCode": 200}) as mock_api:
        handler(event, MagicMock())

    mock_api.assert_called_once_with(event, pytest.approx(MagicMock(), abs=1e9))


def test_combined_handler_routes_rawpath_to_api():
    event = {"rawPath": "/health"}

    with patch("src.lambda_handler.api_gateway_handler", return_value={"statusCode": 200}) as mock_api:
        handler(event, MagicMock())

    mock_api.assert_called_once()


def test_combined_handler_routes_records_to_worker():
    event = {"Records": []}

    with patch("src.lambda_handler.worker_handler", return_value={"batchItemFailures": []}) as mock_worker:
        handler(event, MagicMock())

    mock_worker.assert_called_once()


def test_combined_handler_routes_direct_invocation_to_worker():
    event = {"job_id": "job-1", "pdf_url": "https://example.com/doc.pdf"}

    with patch("src.lambda_handler.worker_handler", return_value=None) as mock_worker:
        handler(event, MagicMock())

    mock_worker.assert_called_once()
