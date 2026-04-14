"""API Gateway handler — POST /process, GET /jobs/{id}, GET /health, GET /docs."""

import asyncio
import dataclasses
import json
import uuid

import boto3
from pydantic import ValidationError

from src.api.schemas import ProcessRequest, ProcessResponse, StatusResponse
from src.models.job import FieldInstruction, JobPayload
from src.shared.exceptions import JobNotFoundError, SSRFBlockedError, ValidationError as OCRValidationError
from src.shared.logging import get_logger
from src.shared.url_validator import validate_url

_logger = get_logger(__name__)

_SWAGGER_HTML = """<!DOCTYPE html>
<html><head><title>OCR AI API</title>
<meta charset="utf-8"/>
<link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
</head><body>
<div id="swagger-ui"></div>
<script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<script>SwaggerUIBundle({url:"/openapi.json",dom_id:"#swagger-ui"});</script>
</body></html>"""


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def _html_response(status_code: int, html: str) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "text/html"},
        "body": html,
    }


async def handle_api_event(event: dict, context: object, container: object) -> dict:
    """Route an API Gateway event to the correct handler."""
    method = event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method", "")
    path = event.get("path") or event.get("rawPath", "/")

    if method == "GET" and path == "/health":
        return _response(200, {"status": "healthy"})

    if method == "GET" and path == "/docs":
        return _html_response(200, _SWAGGER_HTML)

    if method == "GET" and path == "/openapi.json":
        return await _handle_openapi()

    if method == "POST" and path == "/process":
        return await _handle_process(event, container)

    if method == "GET" and path.startswith("/jobs/"):
        job_id = path.split("/jobs/", 1)[1].strip("/")
        return await _handle_get_job(job_id, container)

    return _response(404, {"error": "Not found"})


async def _handle_process(event: dict, container: object) -> dict:
    raw_body = event.get("body") or "{}"
    try:
        body_dict = json.loads(raw_body)
        request = ProcessRequest.model_validate(body_dict)
    except (json.JSONDecodeError, ValidationError) as exc:
        return _response(400, {"error": str(exc)})

    try:
        await validate_url(request.pdf_url)
        if request.callback_url:
            await validate_url(request.callback_url)
    except (SSRFBlockedError, OCRValidationError) as exc:
        return _response(400, {"error": str(exc)})

    job_id = str(uuid.uuid4())
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
        metadata=request.metadata,
    )

    repo = container.get_repo()
    repo.create(job_id, dataclasses.asdict(payload))

    sqs = boto3.client("sqs", region_name=container.settings.aws.region)
    sqs.send_message(
        QueueUrl=container.settings.aws.sqs_queue_url,
        MessageBody=json.dumps(dataclasses.asdict(payload)),
    )

    base_url = container.settings.aws.http_api_base_url.rstrip("/")
    status_url = f"{base_url}/jobs/{job_id}"

    _logger.info("job_received", extra={"job_id": job_id})

    return _response(
        202,
        ProcessResponse(job_id=job_id, status="queued", status_url=status_url).model_dump(),
    )


async def _handle_get_job(job_id: str, container: object) -> dict:
    try:
        item = container.get_repo().get(job_id)
    except JobNotFoundError:
        return _response(404, {"error": f"Job {job_id} not found"})

    progress_raw = item.get("progress")
    progress = None
    if progress_raw:
        progress = {
            "total_pages": progress_raw.get("total_pages", 0),
            "processed_pages": progress_raw.get("processed_pages", 0),
            "current_step": progress_raw.get("current_step", ""),
        }

    return _response(
        200,
        StatusResponse(
            job_id=item["job_id"],
            status=item["status"],
            progress=progress,
            result_url=item.get("result_url"),
            error=item.get("error"),
            created_at=item["created_at"],
            updated_at=item["updated_at"],
        ).model_dump(),
    )


async def _handle_openapi() -> dict:
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "OCR AI Service", "version": "6.0.0"},
        "paths": {
            "/process": {
                "post": {
                    "summary": "Submit a PDF for OCR processing",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ProcessRequest"}}},
                    },
                    "responses": {"202": {"description": "Job queued"}},
                }
            },
            "/jobs/{job_id}": {
                "get": {
                    "summary": "Get job status",
                    "parameters": [{"name": "job_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Job status"}, "404": {"description": "Job not found"}},
                }
            },
            "/health": {"get": {"summary": "Health check", "responses": {"200": {"description": "Healthy"}}}},
        },
    }
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(spec),
    }
