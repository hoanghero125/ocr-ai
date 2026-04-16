"""Lambda entry points. Routes events to api or worker handlers."""

import asyncio
import json
import logging

from src.container import get_container
from src.workers.sqs import handle_direct_invocation, handle_sqs_batch

_logger = logging.getLogger(__name__)


def api_gateway_handler(event: dict, context: object) -> dict:
    """Entry point for the API Lambda (60s, 512 MB)."""
    try:
        from src.api.routes import handle_api_event
        container = get_container()
        return asyncio.run(handle_api_event(event, context, container))
    except Exception as exc:
        _logger.exception("api_gateway_handler_crashed: %s", exc)
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"code": "INTERNAL_ERROR", "message": str(exc)}),
        }


def worker_handler(event: dict, context: object) -> dict | None:
    """Entry point for the worker Lambda (900s, 2048 MB)."""
    container = get_container()
    if "Records" in event:
        return handle_sqs_batch(event, context, container)
    return handle_direct_invocation(event, context, container)


def handler(event: dict, context: object) -> dict | None:
    """Combined entry for local dev and tests — detects event type and routes."""
    if "httpMethod" in event or "rawPath" in event:
        return api_gateway_handler(event, context)
    return worker_handler(event, context)
