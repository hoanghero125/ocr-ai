"""CheckpointManager — save and load mid-job state for Lambda continuations."""

import dataclasses

from src.infra.repository import JobRepository
from src.infra.store import ResultStore
from src.models.job import JobPayload
from src.models.result import PageResult
from src.shared.exceptions import CheckpointError
from src.shared.logging import get_logger


class CheckpointManager:
    def __init__(self, store: ResultStore, repo: JobRepository) -> None:
        self._store = store
        self._repo = repo

    async def save_after_ocr(
        self, job_id: str, pages: list[PageResult], payload: JobPayload
    ) -> JobPayload:
        """
        Persist OCR pages to S3 and record the checkpoint in DynamoDB.
        Returns an updated JobPayload with ocr_checkpoint_key set and continuation_count incremented.
        """
        log = get_logger(__name__, job_id=job_id)
        key = f"checkpoints/{job_id}/ocr.json"

        await self._store.put_pages(key, pages)

        written = self._repo.conditional_write_checkpoint(
            job_id=job_id,
            idempotency_key=f"ocr-{job_id}",
            checkpoint_data={"stage": "ocr", "key": key},
        )

        log.info(
            "checkpoint_saved",
            extra={"scenario": "ocr", "key": key, "new_write": written},
        )

        return dataclasses.replace(
            payload,
            ocr_checkpoint_key=key,
            continuation_count=payload.continuation_count + 1,
        )

    async def save_after_extraction(
        self, job_id: str, pages: list[PageResult], payload: JobPayload
    ) -> JobPayload:
        """
        Persist partially-extracted pages to S3 and record the checkpoint in DynamoDB.
        Uses a per-continuation idempotency key so that duplicate Lambda invocations
        at the same continuation_count cannot save the checkpoint twice.
        Returns an updated JobPayload with extraction_checkpoint_key set and continuation_count incremented.
        """
        log = get_logger(__name__, job_id=job_id)
        key = f"checkpoints/{job_id}/extraction.json"

        await self._store.put_pages(key, pages)

        # Zero-pad continuation_count so lexicographic comparison is correct
        idempotency_key = f"extraction-{job_id}-{payload.continuation_count:03d}"
        written = self._repo.conditional_write_extraction_checkpoint(
            job_id=job_id,
            idempotency_key=idempotency_key,
            checkpoint_data={"stage": "extraction", "key": key},
        )

        log.info(
            "checkpoint_saved",
            extra={"scenario": "extraction", "key": key, "new_write": written},
        )

        return dataclasses.replace(
            payload,
            extraction_checkpoint_key=key,
            continuation_count=payload.continuation_count + 1,
        )

    async def load_ocr_checkpoint(self, key: str) -> list[PageResult]:
        """Load OCR pages from S3 checkpoint."""
        try:
            return await self._store.get_pages(key)
        except Exception as exc:
            raise CheckpointError(f"Failed to load OCR checkpoint: {exc}") from exc

    async def load_extraction_checkpoint(self, key: str) -> list[PageResult]:
        """Load partially-extracted pages from S3 checkpoint."""
        try:
            return await self._store.get_pages(key)
        except Exception as exc:
            raise CheckpointError(f"Failed to load extraction checkpoint: {exc}") from exc

    async def cleanup(self, payload: JobPayload) -> None:
        """Delete S3 checkpoint files for a completed job. Best-effort."""
        if payload.ocr_checkpoint_key:
            await self._store.delete_pages(payload.ocr_checkpoint_key)
        if payload.extraction_checkpoint_key:
            await self._store.delete_pages(payload.extraction_checkpoint_key)
