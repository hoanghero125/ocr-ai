"""SQS batch handler and direct Lambda invocation handler."""

import asyncio
import dataclasses
import json

from src.models.job import FieldInstruction, JobPayload
from src.shared.logging import get_logger

_logger = get_logger(__name__)


def _payload_from_dict(d: dict) -> JobPayload:
    field_instructions = tuple(
        FieldInstruction(
            key=fi["key"],
            label=fi["label"],
            description=fi.get("description", ""),
            min_confidence=fi.get("min_confidence"),
        )
        for fi in d.get("field_instructions") or []
    )
    return JobPayload(
        job_id=d["job_id"],
        pdf_url=d["pdf_url"],
        callback_url=d.get("callback_url"),
        field_instructions=field_instructions,
        options=d.get("options") or {},
        metadata=d.get("metadata") or {},
        continuation_count=d.get("continuation_count", 0),
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
            body = json.loads(record["body"])
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
