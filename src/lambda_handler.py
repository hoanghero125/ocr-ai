"""Lambda entry points. Routes events to api or worker handlers. ≤60 lines."""

import asyncio

from src.container import get_container
from src.workers.sqs import handle_direct_invocation, handle_sqs_batch


def api_gateway_handler(event: dict, context: object) -> dict:
    """Entry point for the API Lambda (60s, 512 MB)."""
    from src.api.routes import handle_api_event
    container = get_container()
    return asyncio.run(handle_api_event(event, context, container))


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
