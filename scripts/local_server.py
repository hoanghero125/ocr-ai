"""
Local dev server — same config as production.

Uses real DynamoDB, real MinIO, and real Mistral.
SQS is skipped: jobs are processed inline as a background task.

Usage:
    python scripts/local_server.py
    # open http://localhost:8000/docs
"""

import asyncio
import dataclasses
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ── Project root on sys.path ──────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ── Config: Docker Compose / shell inject env vars (same keys as Lambda). ─────
os.environ.setdefault("ENVIRONMENT", "local")

# ── Build Container (same wiring as Lambda cold start) ────────────────────────
from src.shared.config import get_settings
from src.container import Container

get_settings.cache_clear()
_container = Container(get_settings())

# ── FastAPI ───────────────────────────────────────────────────────────────────
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from src.api.schemas import ProcessRequest, ProcessResponse
from src.models.job import FieldInstruction, JobPayload
from src.shared.exceptions import JobNotFoundError, SSRFBlockedError, ValidationError as OCRValidationError
from src.shared.url_validator import validate_url
from src.mistral.ocr import OCRStage
from src.mistral.extraction import ExtractionStage

app = FastAPI(
    title="OCR Local (prod config)",
    version="1.0.0",
    description="Same endpoints and infrastructure as production. SQS replaced by inline background processing.",
)


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/process", status_code=202)
async def process(http_request: Request, background_tasks: BackgroundTasks):
    """
    Submit a PDF for processing.
    Same request/response shape as the production API.
    """
    try:
        body_dict = await http_request.json()
        request = ProcessRequest.model_validate(body_dict)
    except (json.JSONDecodeError, ValidationError) as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    try:
        await validate_url(request.pdf_url)
        if request.callback_url:
            await validate_url(request.callback_url)
    except (SSRFBlockedError, OCRValidationError) as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    job_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc)

    field_instructions = tuple(
        FieldInstruction(
            key=fi.key,
            label=fi.label,
            description=fi.description,
            min_confidence=fi.min_confidence,
        )
        for fi in request.field_instructions
    )

    payload = JobPayload(
        job_id=job_id,
        pdf_url=request.pdf_url,
        callback_url=request.callback_url,
        field_instructions=field_instructions,
        options=request.options.model_dump(),
        metadata=request.metadata.model_dump() if request.metadata else {},
    )

    # Create the job record in DynamoDB
    repo = _container.get_repo()
    repo.create(job_id, dataclasses.asdict(payload))

    # Process inline (no SQS) — runs after the response is sent
    background_tasks.add_task(_run_job, payload)

    status_url = f"http://localhost:8000/jobs/{job_id}"
    return ProcessResponse(
        job_id=job_id,
        status="queued",
        status_url=status_url,
        created_at=created_at,
        message="Job queued successfully",
    ).model_dump()


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    """Poll job status — reads directly from DynamoDB."""
    try:
        item = _container.get_repo().get(job_id)
    except JobNotFoundError:
        return JSONResponse(status_code=404, content={"error": f"Job {job_id} not found"})

    return {
        "job_id": item["job_id"],
        "status": item["status"],
        "result_url": item.get("result_url"),
        "progress": item.get("progress"),
        "error": item.get("error"),
        "created_at": item["created_at"],
        "updated_at": item["updated_at"],
    }


async def _run_job(payload: JobPayload) -> None:
    """Run the full OCR pipeline for a job (same code path as the Worker Lambda)."""
    processor = _container.get_processor()
    await processor.process(payload, context=None)


@app.post("/extract", summary="Synchronous OCR + extraction — returns raw data immediately")
async def extract(http_request: Request):
    """
    Process a PDF and return extracted fields directly (no job queue, no polling).
    Same request format as /process. Response matches EXAMPLE_RESPONSE format.
    """
    try:
        body_dict = await http_request.json()
        request = ProcessRequest.model_validate(body_dict)
    except (json.JSONDecodeError, ValidationError) as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    try:
        await validate_url(request.pdf_url)
    except (SSRFBlockedError, OCRValidationError) as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    settings = _container.settings
    field_instructions = [
        FieldInstruction(
            key=fi.key,
            label=fi.label,
            description=fi.description,
            min_confidence=fi.min_confidence,
        )
        for fi in request.field_instructions
    ]

    # Build OCR + extraction stages directly (same as Container internals)
    from src.mistral.client import MistralClient
    client = MistralClient(
        api_key=settings.mistral.api_key,
        ocr_model=settings.mistral.ocr_model,
        chat_model=settings.mistral.chat_model,
        table_format=settings.mistral.table_format,
        base_url=settings.mistral.base_url,
        timeout_s=settings.mistral.timeout_s,
        max_retries=settings.mistral.max_retries,
    )
    ocr_stage = OCRStage(client=client)
    extraction_stage = ExtractionStage(
        client=client,
        max_concurrent_pages=settings.processing.max_concurrent_pages,
        max_retries_per_page=settings.processing.extract_max_retries_per_page,
    )

    job_id = str(uuid.uuid4())

    pages = await ocr_stage.run(pdf_url=request.pdf_url)
    if field_instructions:
        pages = await extraction_stage.run(
            pages=pages,
            field_instructions=tuple(field_instructions),
        )

    page_confidences = [p.confidence for p in pages if p.confidence > 0]
    overall_confidence = (
        round(sum(page_confidences) / len(page_confidences), 3)
        if page_confidences else 0.0
    )

    return {
        "job_id": job_id,
        "status": "completed",
        "total_pages": len(pages),
        "confidence": overall_confidence,
        "pages": [
            {
                "page_number": p.page_number,
                "handwritten_percentage": p.handwritten_percentage,
                "extracted_fields": [
                    {
                        "key": f.key,
                        "label": f.label,
                        "value": f.value,
                        "confidence": f.confidence,
                        "field_type": f.field_type,
                    }
                    for f in p.extracted_fields
                ],
                "auto_fields": [
                    {
                        "key": f.key,
                        "label": f.label,
                        "value": f.value,
                        "confidence": f.confidence,
                        "field_type": f.field_type,
                    }
                    for f in p.auto_fields
                ],
                "tables": [
                    {"headers": t.headers, "rows": t.rows}
                    for t in p.tables
                ],
                "free_texts": [
                    {
                        "content": ft.content,
                        "confidence": ft.confidence,
                        "field_type": ft.field_type,
                        "position": ft.position,
                    }
                    for ft in p.free_texts
                ],
                "confidence": p.confidence,
                "status": p.status,
                "error_message": p.error_message,
                "error_step": p.error_step,
            }
            for p in pages
        ],
    }


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
