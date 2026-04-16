"""JobRepository — all DynamoDB operations for job state."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from boto3.dynamodb.conditions import Attr

from src.models.job import JobStatus
from src.models.result import JobProgress
from src.shared.exceptions import JobNotFoundError

_TTL_DAYS = 30


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _ttl_timestamp() -> int:
    return int((datetime.now(tz=timezone.utc) + timedelta(days=_TTL_DAYS)).timestamp())


def _to_dynamodb_value(value: Any) -> Any:
    """Convert Python values into boto3 DynamoDB-safe values recursively."""
    if value is None or isinstance(value, (str, bytes, bool, int, Decimal)):
        return value
    if isinstance(value, float):
        # boto3 DynamoDB serializer rejects float; Decimal is required for Number.
        return Decimal(str(value))
    if isinstance(value, list):
        return [_to_dynamodb_value(v) for v in value]
    if isinstance(value, tuple):
        return [_to_dynamodb_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_dynamodb_value(v) for k, v in value.items()}
    if isinstance(value, set):
        return {_to_dynamodb_value(v) for v in value}
    return value


class JobRepository:
    def __init__(self, table: Any) -> None:
        """
        Args:
            table: boto3 DynamoDB Table resource
        """
        self._table = table

    def create(self, job_id: str, payload_dict: dict) -> None:
        """Insert a new job record with TTL set 30 days out."""
        now = _now_iso()
        self._table.put_item(
            Item={
                "job_id": job_id,
                "status": JobStatus.QUEUED.value,
                "payload": _to_dynamodb_value(payload_dict),
                "progress": None,
                "checkpoint": None,
                "result_url": None,
                "error": None,
                "created_at": now,
                "updated_at": now,
                "ttl": _ttl_timestamp(),
            }
        )

    def get(self, job_id: str) -> dict:
        """Return the job record. Raise JobNotFoundError if missing."""
        response = self._table.get_item(Key={"job_id": job_id})
        item = response.get("Item")
        if item is None:
            raise JobNotFoundError(job_id)
        return item

    def update_status(self, job_id: str, status: JobStatus, **extra: Any) -> None:
        """Update job status plus any extra fields (result_url, error, etc.)."""
        expressions = ["#st = :status", "updated_at = :updated_at"]
        names = {"#st": "status"}
        values: dict[str, Any] = {
            ":status": status.value,
            ":updated_at": _now_iso(),
        }

        for key, value in extra.items():
            placeholder = f":extra_{key}"
            expressions.append(f"{key} = {placeholder}")
            values[placeholder] = _to_dynamodb_value(value)

        self._table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET " + ", ".join(expressions),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )

    def update_progress(self, job_id: str, progress: JobProgress) -> None:
        """Update the progress sub-map during active processing."""
        self._table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET progress = :progress, updated_at = :updated_at",
            ExpressionAttributeValues={
                    ":progress": _to_dynamodb_value({
                    "total_pages": progress.total_pages,
                    "processed_pages": progress.processed_pages,
                    "current_step": progress.current_step,
                    }),
                ":updated_at": _now_iso(),
            },
        )

    def conditional_write_checkpoint(
        self,
        job_id: str,
        idempotency_key: str,
        checkpoint_data: dict,
    ) -> bool:
        """
        Write OCR checkpoint only if idempotency_key attribute does not already exist.
        Returns True if written, False if the key already existed (duplicate invocation).
        """
        from botocore.exceptions import ClientError

        try:
            self._table.update_item(
                Key={"job_id": job_id},
                UpdateExpression=(
                    "SET checkpoint = :checkpoint, "
                    "idempotency_key = :ikey, "
                    "updated_at = :updated_at"
                ),
                ConditionExpression=Attr("idempotency_key").not_exists(),
                ExpressionAttributeValues={
                    ":checkpoint": _to_dynamodb_value(checkpoint_data),
                    ":ikey": idempotency_key,
                    ":updated_at": _now_iso(),
                },
            )
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def conditional_write_extraction_checkpoint(
        self,
        job_id: str,
        idempotency_key: str,
        checkpoint_data: dict,
    ) -> bool:
        """
        Write extraction checkpoint only if this continuation hasn't been recorded yet.

        Uses a separate extraction_idempotency_key attribute with a not_exists OR lt
        condition, allowing forward progress across continuations while preventing
        duplicate writes within the same continuation invocation.

        Returns True if written, False if this continuation was already checkpointed.
        """
        from botocore.exceptions import ClientError

        try:
            self._table.update_item(
                Key={"job_id": job_id},
                UpdateExpression=(
                    "SET checkpoint = :checkpoint, "
                    "extraction_idempotency_key = :ikey, "
                    "updated_at = :updated_at"
                ),
                ConditionExpression=(
                    Attr("extraction_idempotency_key").not_exists()
                    | Attr("extraction_idempotency_key").lt(idempotency_key)
                ),
                ExpressionAttributeValues={
                    ":checkpoint": _to_dynamodb_value(checkpoint_data),
                    ":ikey": idempotency_key,
                    ":updated_at": _now_iso(),
                },
            )
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
