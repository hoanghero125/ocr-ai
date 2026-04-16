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

# ── Load .env ─────────────────────────────────────────────────────────────────
env_path = PROJECT_ROOT / ".env"
if env_path.exists():
    from dotenv import load_dotenv
    load_dotenv(env_path)
else:
    print("[local] No .env found — copy .env.example to .env and fill in credentials")
    sys.exit(1)

os.environ.setdefault("ENVIRONMENT", "local")

# ── Build Container (same wiring as Lambda cold start) ────────────────────────
from src.shared.config import get_settings
from src.container import Container

get_settings.cache_clear()
_container = Container(get_settings())

# ── FastAPI ───────────────────────────────────────────────────────────────────
import time as _time

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from src.api.schemas import ProcessRequest, ProcessResponse
from src.models.job import FieldInstruction, JobPayload
from src.shared.exceptions import JobNotFoundError, SSRFBlockedError, ValidationError as OCRValidationError
from src.shared.logging import get_logger
from src.shared.url_validator import validate_url
from src.mistral.ocr import OCRStage
from src.mistral.extraction import ExtractionStage

_log = get_logger(__name__)

app = FastAPI(
    title="OCR Local (prod config)",
    version="1.0.0",
    description="Same endpoints and infrastructure as production. SQS replaced by inline background processing.",
)


@app.middleware("http")
async def _log_requests(request: Request, call_next):
    t0 = _time.monotonic()
    response = await call_next(request)
    ms = int((_time.monotonic() - t0) * 1000)
    _log.info(
        "request",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": ms,
        },
    )
    return response


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": code, "message": message})


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"code": "INTERNAL_ERROR", "message": str(exc)},
    )


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/process", status_code=202)
async def process(http_request: Request, background_tasks: BackgroundTasks):
    """Submit a PDF for async processing — returns job_id immediately."""
    try:
        body_dict = await http_request.json()
        request = ProcessRequest.model_validate(body_dict)
    except json.JSONDecodeError:
        return _err(400, "INVALID_JSON", "Request body is not valid JSON")
    except ValidationError as exc:
        return _err(400, "VALIDATION_ERROR", str(exc))

    try:
        await validate_url(request.pdf_url)
        if request.callback_url:
            await validate_url(request.callback_url)
    except SSRFBlockedError:
        return _err(400, "URL_NOT_ALLOWED", "pdf_url or callback_url resolves to a disallowed address")
    except OCRValidationError as exc:
        return _err(400, "INVALID_URL", str(exc))

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

    try:
        _container.get_repo().create(job_id, dataclasses.asdict(payload))
    except Exception as exc:
        return _err(503, "DATABASE_ERROR", f"Failed to create job record: {exc}")

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
        return _err(404, "JOB_NOT_FOUND", f"Job {job_id} not found")
    except Exception as exc:
        return _err(503, "DATABASE_ERROR", f"Failed to retrieve job: {exc}")

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
    except json.JSONDecodeError:
        return _err(400, "INVALID_JSON", "Request body is not valid JSON")
    except ValidationError as exc:
        return _err(400, "VALIDATION_ERROR", str(exc))

    try:
        await validate_url(request.pdf_url)
    except SSRFBlockedError:
        return _err(400, "URL_NOT_ALLOWED", "pdf_url resolves to a disallowed address")
    except OCRValidationError as exc:
        return _err(400, "INVALID_URL", str(exc))

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

    try:
        pages = await ocr_stage.run(pdf_url=request.pdf_url)
    except Exception as exc:
        return _err(502, "OCR_FAILED", f"OCR stage failed: {exc}")

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
