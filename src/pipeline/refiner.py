"""RefineHandler — re-extracts specific fields using stored pages_markdown, no re-OCR."""

import dataclasses

from src.infra.repository import JobRepository
from src.infra.store import ResultStore
from src.mistral.extraction import ExtractionStage
from src.models.job import FieldInstruction
from src.models.result import PageResult, aggregate_extracted_fields
from src.shared.logging import get_logger


class RefineHandler:
    def __init__(
        self,
        store: ResultStore,
        extraction_stage: ExtractionStage,
        repo: JobRepository,
    ) -> None:
        self._store = store
        self._extraction_stage = extraction_stage
        self._repo = repo

    async def refine(
        self,
        job_id: str,
        field_instructions: tuple[FieldInstruction, ...],
    ) -> dict:
        """
        Re-extract fields from a completed job's stored markdown.

        Raises:
            JobNotFoundError: job does not exist
            ValueError: job is not in a completed state, or no markdown stored
            Exception: MinIO I/O or Mistral extraction errors
        """
        log = get_logger(__name__, job_id=job_id)

        item = self._repo.get(job_id)  # raises JobNotFoundError if missing
        status = item.get("status", "")
        if status not in ("completed", "completed_with_errors"):
            raise ValueError(f"Job cannot be refined — current status: {status!r}")

        stored = self._store.get_result(job_id)
        pages_markdown: list[str] = stored.get("pages_markdown") or []
        original_fields: list[dict] = stored.get("extracted_fields") or []

        if not pages_markdown:
            raise ValueError(
                "No OCR text stored for this job — cannot refine "
                "(job may have been processed before pages_markdown was introduced)"
            )

        pages = [
            PageResult(page_number=i + 1, markdown=md)
            for i, md in enumerate(pages_markdown)
        ]

        log.info("refine_start", extra={"pages": len(pages), "fields": len(field_instructions)})

        refined_pages = await self._extraction_stage.run(
            pages=pages,
            field_instructions=field_instructions,
            job_id=job_id,
        )

        refined_fields = aggregate_extracted_fields(refined_pages)

        # Merge: original fields as base, refined fields overwrite by key only
        merged: dict[str, dict] = {f["key"]: f for f in original_fields}
        for rf in refined_fields:
            merged[rf.key] = dataclasses.asdict(rf)

        stored["extracted_fields"] = list(merged.values())
        self._store.put_result_raw(job_id, stored)

        log.info("refine_complete", extra={"refined_count": len(refined_fields)})

        return {
            "job_id": job_id,
            "refined_fields": [dataclasses.asdict(rf) for rf in refined_fields],
            "pages_reprocessed": len(pages),
        }
