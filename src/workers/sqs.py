"""SQS batch handler and direct Lambda invocation handler."""

import asyncio
import dataclasses
import json

from src.models.job import FieldInstruction, JobPayload
from src.shared.logging import get_logger

_logger = get_logger(__name__)
_MAX_CONTINUATION_COUNT = 100  # hard ceiling — guards against corrupt/replayed messages


def _payload_from_dict(d: dict) -> JobPayload:
    field_instructions = tuple(
        FieldInstruction(
            key=fi["key"],
            label=fi["label"],
            description=fi.get("description", ""),
            min_confidence=fi.get("min_confidence"),
            data_type=fi.get("data_type"),
        )
        for fi in d.get("field_instructions") or []
    )
    raw_count = d.get("continuation_count", 0)
    continuation_count = max(0, min(int(raw_count), _MAX_CONTINUATION_COUNT))

    return JobPayload(
        job_id=d["job_id"],
        pdf_url=d["pdf_url"],
        callback_url=d.get("callback_url"),
        field_instructions=field_instructions,
        options=d.get("options") or {},
        metadata=d.get("metadata") or {},
        continuation_count=continuation_count,
        ocr_checkpoint_key=d.get("ocr_checkpoint_key"),
        extraction_checkpoint_key=d.get("extraction_checkpoint_key"),
    )


def handle_sqs_batch(event: dict, context: object, container: object) -> dict:
    """
    Process a batch of SQS records.
    Returns partial failure response so only failed messages are re-queued.
    """
    records = event.get("Records", [])
    batch_item_failures = []

    for record in records:
        message_id = record.get("messageId", "unknown")
        try:
            raw_body = record.get("body")
            if not raw_body:
                raise ValueError("SQS record missing 'body'")
            body = json.loads(raw_body)
            payload = _payload_from_dict(body)
            processor = container.get_processor()
            asyncio.run(processor.process(payload, context=context))
        except Exception as exc:
            _logger.error(
                "sqs_record_failed",
                extra={"message_id": message_id, "error": str(exc)},
            )
            batch_item_failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": batch_item_failures}


def handle_direct_invocation(event: dict, context: object, container: object) -> None:
    """
    Handle a direct Lambda invocation (continuation path).
    Event body is the same JSON structure as an SQS message body.
    """
    try:
        payload = _payload_from_dict(event)
        processor = container.get_processor()
        asyncio.run(processor.process(payload, context=context))
    except Exception as exc:
        _logger.error(
            "direct_invocation_failed",
            extra={"job_id": event.get("job_id"), "error": str(exc)},
        )
        raise
