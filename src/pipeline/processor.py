"""OCRProcessor — orchestrates all pipeline stages, checkpoints, progress, and callbacks."""

import time
from typing import Any

from src.checkpoint.manager import CheckpointManager
from src.infra.repository import JobRepository
from src.infra.store import ResultStore
from src.infra.webhook import WebhookClient
from src.mistral.extraction import ExtractionStage
from src.mistral.ocr import OCRStage
from src.models.job import JobPayload, JobStatus
from src.models.result import JobProgress, OCRResult, PageResult
from src.pipeline.continuation import ContinuationTrigger
from src.shared.config import Settings
from src.shared.logging import get_logger


class OCRProcessor:
    def __init__(
        self,
        ocr_stage: OCRStage,
        extraction_stage: ExtractionStage,
        checkpoint_manager: CheckpointManager,
        repo: JobRepository,
        store: ResultStore,
        webhook: WebhookClient,
        continuation: ContinuationTrigger,
        settings: Settings,
    ) -> None:
        self._ocr = ocr_stage
        self._extraction = extraction_stage
        self._checkpoint = checkpoint_manager
        self._repo = repo
        self._store = store
        self._webhook = webhook
        self._continuation = continuation
        self._settings = settings

    def _remaining_ms(self, context: Any) -> int:
        if context and hasattr(context, "get_remaining_time_in_millis"):
            return context.get_remaining_time_in_millis()
        return 999_999_999  # effectively unlimited (local dev / tests)

    def _near_timeout(self, context: Any) -> bool:
        if not self._settings.processing.lambda_extract_continuation_enabled:
            return False
        return self._remaining_ms(context) < self._settings.processing.lambda_time_buffer_ms

    async def process(self, payload: JobPayload, context: Any = None) -> None:
        """
        Run the full OCR pipeline for a job.
        Handles checkpoints, timeouts, progress updates, and the webhook callback.
        """
        job_id = payload.job_id
        log = get_logger(__name__, job_id=job_id)
        t0 = time.monotonic()

        try:
            self._repo.update_status(job_id, JobStatus.PROCESSING)
            log.info("job_received")

            # ── STAGE 1: OCR ───────────────────────────────────────────
            if payload.ocr_checkpoint_key:
                log.info("ocr_checkpoint_hit", extra={"key": payload.ocr_checkpoint_key})
                pages = self._checkpoint.load_ocr_checkpoint(payload.ocr_checkpoint_key)
            else:
                pages = await self._ocr.run(payload.pdf_url, job_id=job_id)
                self._repo.update_progress(
                    job_id,
                    JobProgress(
                        total_pages=len(pages),
                        processed_pages=0,
                        current_step="OCR complete, starting extraction",
                    ),
                )

            # ── TIMEOUT CHECK (after OCR) ──────────────────────────────
            if self._near_timeout(context) and not payload.ocr_checkpoint_key:
                new_payload = await self._checkpoint.save_after_ocr(job_id, pages, payload)
                await self._continuation.invoke(new_payload, job_id=job_id)
                return

            # ── STAGE 2: EXTRACTION ────────────────────────────────────
            if payload.field_instructions:
                start_pages = pages
                if payload.extraction_checkpoint_key:
                    log.info(
                        "extraction_checkpoint_hit",
                        extra={"key": payload.extraction_checkpoint_key},
                    )
                    start_pages = self._checkpoint.load_extraction_checkpoint(
                        payload.extraction_checkpoint_key
                    )
                    # Only re-extract pages that have no fields yet
                    pending = [p for p in start_pages if not p.fields and not p.error]
                    done = [p for p in start_pages if p.fields or p.error]
                else:
                    pending = pages
                    done = []

                total = len(pages)

                def on_page_done(page_num: int, _total: int) -> None:
                    processed = len(done) + sum(
                        1 for p in start_pages
                        if p.page_number == page_num or p.fields or p.error
                    )
                    self._repo.update_progress(
                        job_id,
                        JobProgress(
                            total_pages=total,
                            processed_pages=min(processed, total),
                            current_step=f"Extracting fields {min(processed, total)}/{total}",
                        ),
                    )

                extracted_pending = await self._extraction.run(
                    pages=pending,
                    field_instructions=payload.field_instructions,
                    on_page_done=on_page_done,
                    job_id=job_id,
                )

                # Merge done + newly extracted, sorted by page_number
                pages_by_num = {p.page_number: p for p in done}
                pages_by_num.update({p.page_number: p for p in extracted_pending})
                pages = [pages_by_num[n] for n in sorted(pages_by_num)]

                # ── TIMEOUT CHECK (after partial extraction) ───────────
                if self._near_timeout(context):
                    new_payload = await self._checkpoint.save_after_extraction(
                        job_id, pages, payload
                    )
                    await self._continuation.invoke(new_payload, job_id=job_id)
                    return

            # ── FINALIZE ───────────────────────────────────────────────
            errors = [p.error for p in pages if p.error]
            status = (
                JobStatus.COMPLETED_WITH_ERRORS if errors else JobStatus.COMPLETED
            )

            result = OCRResult(
                job_id=job_id,
                status=status.value,
                pages=pages,
                total_pages=len(pages),
                processed_pages=len(pages),
                errors=[e for e in errors if e],
                metadata=payload.metadata,
            )

            result_url = self._store.put_result(job_id, result)
            self._repo.update_status(
                job_id,
                status,
                result_url=result_url,
                progress={
                    "total_pages": len(pages),
                    "processed_pages": len(pages),
                    "current_step": "Processing complete",
                },
            )

            duration_ms = int((time.monotonic() - t0) * 1000)
            log.info(
                "job_complete",
                extra={"status": status.value, "total_duration_ms": duration_ms},
            )

            if payload.callback_url:
                try:
                    await self._webhook.send(
                        payload.callback_url,
                        {
                            "job_id": job_id,
                            "status": status.value,
                            "result_url": result_url,
                            "errors": result.errors,
                            "metadata": payload.metadata,
                        },
                        job_id=job_id,
                    )
                except Exception as webhook_exc:
                    log.warning("webhook_send_error", extra={"error": str(webhook_exc)})

        except Exception as exc:
            log.error("processor_failed", extra={"error": str(exc)}, exc_info=True)
            self._repo.update_status(job_id, JobStatus.FAILED, error=str(exc))

            if payload.callback_url:
                try:
                    await self._webhook.send(
                        payload.callback_url,
                        {"job_id": job_id, "status": "failed", "error": str(exc)},
                        job_id=job_id,
                    )
                except Exception as webhook_exc:
                    log.warning("webhook_send_error", extra={"error": str(webhook_exc)})
            raise
