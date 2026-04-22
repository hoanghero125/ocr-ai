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
import time as _time

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from src.api.schemas import ProcessRequest, ProcessResponse, RefineRequest
from src.models.job import FieldInstruction, JobPayload
from src.models.result import aggregate_extracted_fields
from src.shared import codes
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


def _err(status: int, code: int, message: str) -> JSONResponse:
    _log.warning("error_response", extra={"status": status, "code": code, "message": message})
    return JSONResponse(status_code=status, content={"code": code, "message": message})


def _require_auth_or_401(http_request: Request) -> JSONResponse | None:
    """Enforce Bearer auth when API_TOKEN is set (same behavior as Lambda handler)."""
    expected = os.environ.get("API_TOKEN", "")
    if not expected:
        return None
    auth_header = http_request.headers.get("authorization", "")
    parts = auth_header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or parts[1] != expected:
        return _err(401, codes.UNAUTHORIZED, "Missing or invalid Bearer token")
    return None


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    _log.error(
        "unhandled_error",
        extra={"code": codes.INTERNAL_ERROR, "method": request.method, "path": request.url.path, "error": str(exc)},
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={"code": codes.INTERNAL_ERROR, "message": str(exc)},
    )


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/process", status_code=202)
async def process(http_request: Request, background_tasks: BackgroundTasks):
    """Submit a PDF for async processing — returns job_id immediately."""
    if (denied := _require_auth_or_401(http_request)) is not None:
        return denied

    try:
        body_dict = await http_request.json()
        request = ProcessRequest.model_validate(body_dict)
    except json.JSONDecodeError:
        return _err(400, codes.INVALID_JSON, "Request body is not valid JSON")
    except ValidationError as exc:
        return _err(400, codes.VALIDATION_ERROR, str(exc))

    try:
        await validate_url(request.pdf_url)
        if request.callback_url:
            await validate_url(request.callback_url)
    except SSRFBlockedError:
        return _err(400, codes.URL_NOT_ALLOWED, "pdf_url or callback_url resolves to a disallowed address")
    except OCRValidationError as exc:
        return _err(400, codes.INVALID_URL, str(exc))

    job_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc)

    field_instructions = tuple(
        FieldInstruction(
            key=fi.key,
            label=fi.label,
            description=fi.description,
            min_confidence=fi.min_confidence,
            data_type=fi.data_type,
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
        return _err(503, codes.DATABASE_ERROR, f"Failed to create job record: {exc}")

    background_tasks.add_task(_run_job, payload)

    _log.info("job_received", extra={"job_id": job_id})

    base_url = _container.settings.aws.http_api_base_url.rstrip("/")
    status_url = f"{base_url}/jobs/{job_id}"
    return ProcessResponse(
        job_id=job_id,
        status="queued",
        status_url=status_url,
        created_at=created_at,
        message="Job queued successfully",
    ).model_dump()


@app.get("/jobs/{job_id}")
async def get_job(job_id: str, http_request: Request):
    """Poll job status — reads directly from DynamoDB."""
    if (denied := _require_auth_or_401(http_request)) is not None:
        return denied

    try:
        item = _container.get_repo().get(job_id)
    except JobNotFoundError:
        return _err(404, codes.JOB_NOT_FOUND, f"Job {job_id} not found")
    except Exception as exc:
        return _err(503, codes.DATABASE_ERROR, f"Failed to retrieve job: {exc}")

    _log.info("job_status_queried", extra={"job_id": job_id, "status": item["status"]})

    error_code = item.get("error_code")
    return {
        "code": error_code if error_code is not None else codes.SUCCESS,
        "job_id": item["job_id"],
        "status": item["status"],
        "result_url": item.get("result_url"),
        "progress": item.get("progress"),
        "error_code": error_code,
        "error": item.get("error"),
        "created_at": item["created_at"],
        "updated_at": item["updated_at"],
    }


async def _run_job(payload: JobPayload) -> None:
    """Run the full OCR pipeline for a job (same code path as the Worker Lambda)."""
    processor = _container.get_processor()
    await processor.process(payload, context=None)


@app.post("/jobs/{job_id}/refine", summary="Re-extract fields with a correction hint")
async def refine_job(job_id: str, http_request: Request):
    """
    Re-run extraction on a completed job using stored OCR text — no re-OCR cost.
    Pass field_instructions with a description that steers the model toward the correct value.
    Only the listed fields are re-extracted; the stored result is updated in-place.
    """
    if (denied := _require_auth_or_401(http_request)) is not None:
        return denied

    try:
        body_dict = await http_request.json()
        request = RefineRequest.model_validate(body_dict)
    except json.JSONDecodeError:
        return _err(400, codes.INVALID_JSON, "Request body is not valid JSON")
    except ValidationError as exc:
        return _err(400, codes.VALIDATION_ERROR, str(exc))

    field_instructions = tuple(
        FieldInstruction(
            key=fi.key,
            label=fi.label,
            description=fi.description,
            min_confidence=fi.min_confidence,
            data_type=fi.data_type,
        )
        for fi in request.field_instructions
    )

    try:
        result = await _container.get_refiner().refine(job_id, field_instructions)
        return {"code": codes.SUCCESS, **result}
    except JobNotFoundError:
        return _err(404, codes.JOB_NOT_FOUND, f"Job {job_id} not found")
    except ValueError as exc:
        return _err(400, codes.VALIDATION_ERROR, str(exc))
    except Exception as exc:
        _log.error("refine_failed", extra={"code": codes.INTERNAL_ERROR, "job_id": job_id, "error": str(exc)}, exc_info=True)
        return _err(500, codes.INTERNAL_ERROR, f"Refine failed: {exc}")


@app.post("/extract", summary="Synchronous OCR + extraction — returns raw data immediately")
async def extract(http_request: Request):
    """
    Process a PDF and return extracted fields directly (no job queue, no polling).
    Same request format as /process. Response matches EXAMPLE_RESPONSE format.
    """
    if (denied := _require_auth_or_401(http_request)) is not None:
        return denied

    try:
        body_dict = await http_request.json()
        request = ProcessRequest.model_validate(body_dict)
    except json.JSONDecodeError:
        return _err(400, codes.INVALID_JSON, "Request body is not valid JSON")
    except ValidationError as exc:
        return _err(400, codes.VALIDATION_ERROR, str(exc))

    try:
        await validate_url(request.pdf_url)
    except SSRFBlockedError:
        return _err(400, codes.URL_NOT_ALLOWED, "pdf_url resolves to a disallowed address")
    except OCRValidationError as exc:
        return _err(400, codes.INVALID_URL, str(exc))

    settings = _container.settings
    field_instructions = [
        FieldInstruction(
            key=fi.key,
            label=fi.label,
            description=fi.description,
            min_confidence=fi.min_confidence,
            data_type=fi.data_type,
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
        return _err(502, codes.OCR_FAILED, f"OCR stage failed: {exc}")

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
        "extracted_fields": [
            {
                "key": f.key,
                "label": f.label,
                "value": f.value,
                "confidence": f.confidence,
                "field_type": f.field_type,
            }
            for f in aggregate_extracted_fields(pages)
        ],
    }


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
