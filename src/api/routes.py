"""API Gateway handler — POST /process, GET /jobs/{id}, GET /health, GET /docs."""

import asyncio
import copy
import dataclasses
import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3
from pydantic import ValidationError

from src.api.schemas import ProcessRequest, ProcessResponse, StatusResponse
from src.models.job import FieldInstruction, JobPayload
from src.shared.exceptions import JobNotFoundError, SSRFBlockedError, ValidationError as OCRValidationError
from src.shared.logging import get_logger
from src.shared.url_validator import validate_url

_logger = get_logger(__name__)

_SWAGGER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>OCR AI API</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css" />
  <style>body { margin: 0; }</style>
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script>
    window.onload = function() {
      window.ui = SwaggerUIBundle({
        url: "/openapi.json",
        dom_id: "#swagger-ui",
        deepLinking: true,
        presets: [SwaggerUIBundle.presets.apis],
        layout: "BaseLayout"
      });
    };
  </script>
</body>
</html>"""


def _rewrite_defs_to_components(obj: Any) -> None:
    """Rewrite Pydantic v2 #/$defs/X refs to OpenAPI #/components/schemas/X."""
    if isinstance(obj, dict):
        ref = obj.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            obj["$ref"] = "#/components/schemas/" + ref[len("#/$defs/"):]
        for v in obj.values():
            _rewrite_defs_to_components(v)
    elif isinstance(obj, list):
        for item in obj:
            _rewrite_defs_to_components(item)


def _build_openapi_spec() -> dict:
    schema = copy.deepcopy(ProcessRequest.model_json_schema())
    defs = schema.pop("$defs", {})
    root_name = schema.get("title") or "ProcessRequest"
    components: dict[str, Any] = {name: copy.deepcopy(s) for name, s in defs.items()}
    components[root_name] = schema
    _rewrite_defs_to_components(components)

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "OCR AI API",
            "version": "1.0.0",
            "description": (
                "PDF OCR extraction service powered by Mistral.\n\n"
                "Submit a PDF with `POST /process` — returns a `job_id` immediately. "
                "Poll `GET /jobs/{job_id}` (or use the `status_url` from the response) "
                "until status is terminal (`completed`, `completed_with_errors`, `failed`). "
                "Optionally provide a `callback_url` to receive one webhook POST when the job finishes."
            ),
        },
        "components": {"schemas": components},
        "paths": {
            "/process": {
                "post": {
                    "summary": "Submit a PDF for OCR processing",
                    "operationId": "queueOcr",
                    "description": (
                        "Returns **202** immediately. Processing runs asynchronously. "
                        "Use `status_url` or `GET /jobs/{job_id}` to poll for results."
                    ),
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": f"#/components/schemas/{root_name}"}
                            }
                        },
                    },
                    "responses": {
                        "202": {
                            "description": "Job accepted",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "job_id": {"type": "string", "format": "uuid"},
                                            "status": {"type": "string", "example": "queued"},
                                            "status_url": {"type": "string"},
                                            "created_at": {"type": "string", "format": "date-time"},
                                            "message": {"type": "string"},
                                        },
                                    }
                                }
                            },
                        },
                        "400": {"description": "Validation error"},
                    },
                }
            },
            "/jobs/{job_id}": {
                "get": {
                    "summary": "Get job status",
                    "operationId": "jobStatus",
                    "description": (
                        "Poll until `status` is terminal: `completed`, `completed_with_errors`, or `failed`. "
                        "When complete, `result_url` points to the full JSON result in MinIO."
                    ),
                    "parameters": [
                        {
                            "name": "job_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "format": "uuid"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Job found",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "job_id": {"type": "string"},
                                            "status": {"type": "string"},
                                            "result_url": {"type": "string", "nullable": True},
                                            "progress": {
                                                "type": "object",
                                                "nullable": True,
                                                "properties": {
                                                    "total_pages": {"type": "integer"},
                                                    "processed_pages": {"type": "integer"},
                                                    "current_step": {"type": "string"},
                                                },
                                            },
                                            "error": {"type": "string", "nullable": True},
                                            "created_at": {"type": "string"},
                                            "updated_at": {"type": "string"},
                                        },
                                    }
                                }
                            },
                        },
                        "404": {"description": "Job not found"},
                    },
                }
            },
            "/health": {
                "get": {
                    "summary": "Health check",
                    "operationId": "health",
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"status": {"type": "string"}},
                                    }
                                }
                            },
                        }
                    },
                }
            },
        },
    }


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def _error(status_code: int, code: str, message: str) -> dict:
    return _response(status_code, {"code": code, "message": message})


def _log_request(method: str, path: str, status_code: int, t0: float) -> None:
    ms = int((time.monotonic() - t0) * 1000)
    _logger.info(
        "request",
        extra={"method": method, "path": path, "status": status_code, "duration_ms": ms},
    )


def _html_response(status_code: int, html: str) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "text/html"},
        "body": html,
    }


_PUBLIC_PATHS = {"/health", "/docs", "/openapi.json"}


def _check_auth(event: dict) -> dict | None:
    """Return a 401 response if the Bearer token is missing or invalid, else None."""
    expected = os.environ.get("API_TOKEN", "")
    if not expected:
        return None  # auth disabled when API_TOKEN is not set

    headers = event.get("headers") or {}
    # API Gateway v2 lowercases all header names
    auth_header = headers.get("authorization") or headers.get("Authorization") or ""
    parts = auth_header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or parts[1] != expected:
        return _error(401, "UNAUTHORIZED", "Missing or invalid Bearer token")
    return None


async def handle_api_event(event: dict, context: object, container: object) -> dict:
    """Route an API Gateway event to the correct handler."""
    try:
        method = event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method", "")
        path = event.get("path") or event.get("rawPath", "/")
        t0 = time.monotonic()

        if method == "GET" and path == "/health":
            return _response(200, {"status": "healthy"})

        if method in ("GET", "HEAD") and path == "/docs":
            return _html_response(200, _SWAGGER_HTML)

        if method in ("GET", "HEAD") and path == "/openapi.json":
            return await _handle_openapi()

        # Auth required for all other endpoints
        if path not in _PUBLIC_PATHS:
            if (denied := _check_auth(event)) is not None:
                _log_request(method, path, denied["statusCode"], t0)
                return denied

        if method == "POST" and path == "/process":
            result = await _handle_process(event, container)
        elif method == "GET" and path.startswith("/jobs/"):
            job_id = path.split("/jobs/", 1)[1].strip("/")
            result = await _handle_get_job(job_id, container)
        else:
            result = _error(404, "NOT_FOUND", f"Route {method} {path} not found")

        _log_request(method, path, result["statusCode"], t0)
        return result

    except Exception as exc:
        _logger.error("unhandled_error", extra={"error": str(exc)}, exc_info=True)
        result = _error(500, "INTERNAL_ERROR", str(exc))
        _log_request(method if "method" in dir() else "?", path if "path" in dir() else "/", 500, t0 if "t0" in dir() else time.monotonic())
        return result


async def _handle_process(event: dict, container: object) -> dict:
    raw_body = event.get("body") or "{}"
    try:
        body_dict = json.loads(raw_body)
        request = ProcessRequest.model_validate(body_dict)
    except json.JSONDecodeError:
        return _error(400, "INVALID_JSON", "Request body is not valid JSON")
    except ValidationError as exc:
        return _error(400, "VALIDATION_ERROR", str(exc))

    try:
        await validate_url(request.pdf_url)
        if request.callback_url:
            await validate_url(request.callback_url)
    except SSRFBlockedError:
        return _error(400, "URL_NOT_ALLOWED", "pdf_url or callback_url resolves to a disallowed address")
    except OCRValidationError as exc:
        return _error(400, "INVALID_URL", str(exc))

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
        repo = container.get_repo()
        repo.create(job_id, dataclasses.asdict(payload))
    except Exception as exc:
        _logger.error("dynamodb_create_failed", extra={"job_id": job_id, "error": str(exc)}, exc_info=True)
        return _error(503, "DATABASE_ERROR", "Failed to create job record — please retry")

    try:
        sqs = boto3.client("sqs", region_name=container.settings.aws.region)
        sqs.send_message(
            QueueUrl=container.settings.aws.sqs_queue_url,
            MessageBody=json.dumps(dataclasses.asdict(payload)),
        )
    except Exception as exc:
        _logger.error("sqs_send_failed", extra={"job_id": job_id, "error": str(exc)}, exc_info=True)
        return _error(503, "QUEUE_ERROR", "Failed to queue job — please retry")

    base_url = container.settings.aws.http_api_base_url.rstrip("/")
    status_url = f"{base_url}/jobs/{job_id}"

    _logger.info("job_received", extra={"job_id": job_id})

    return _response(
        202,
        ProcessResponse(
            job_id=job_id,
            status="queued",
            status_url=status_url,
            created_at=created_at,
            message="Job queued successfully",
        ).model_dump(),
    )


async def _handle_get_job(job_id: str, container: object) -> dict:
    try:
        item = container.get_repo().get(job_id)
    except JobNotFoundError:
        return _error(404, "JOB_NOT_FOUND", f"Job {job_id} not found")
    except Exception as exc:
        _logger.error("dynamodb_get_failed", extra={"job_id": job_id, "error": str(exc)}, exc_info=True)
        return _error(503, "DATABASE_ERROR", "Failed to retrieve job — please retry")

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
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(_build_openapi_spec()),
    }
