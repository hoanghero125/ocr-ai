"""Structured JSON logger. Redacts secrets. Used throughout the service."""

import json
import logging
import os
import traceback
from datetime import datetime, timezone
from typing import Any

_REDACTED = "***REDACTED***"
_SENSITIVE_KEYS = {"api_key", "password", "token", "authorization", "callback_url"}
_PDF_URL_KEY = "pdf_url"


def _redact(data: dict) -> dict:
    """Return a copy of data with sensitive fields replaced."""
    out = {}
    for k, v in data.items():
        if k.lower() in _SENSITIVE_KEYS:
            out[k] = _REDACTED
        elif isinstance(v, dict):
            out[k] = _redact(v)
        else:
            out[k] = v
    return out


class _JSONFormatter(logging.Formatter):
    def __init__(self, environment: str):
        super().__init__()
        self.environment = environment

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
            "environment": self.environment,
        }

        # job_id injected via LoggerAdapter extra
        job_id = getattr(record, "job_id", None)
        if job_id:
            payload["job_id"] = job_id

        # Any extra fields passed in
        skip = {
            "name", "msg", "args", "levelname", "levelno", "pathname",
            "filename", "module", "exc_info", "exc_text", "stack_info",
            "lineno", "funcName", "created", "msecs", "relativeCreated",
            "thread", "threadName", "processName", "process", "message",
            "taskName", "job_id",
        }
        for k, v in record.__dict__.items():
            if k not in skip:
                payload[k] = v

        # pdf_url is only allowed at DEBUG
        if _PDF_URL_KEY in payload and record.levelno > logging.DEBUG:
            del payload[_PDF_URL_KEY]

        payload = _redact(payload)

        if record.exc_info:
            payload["traceback"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def _build_handler(environment: str) -> logging.Handler:
    handler = logging.StreamHandler()
    handler.setFormatter(_JSONFormatter(environment))
    return handler


def get_logger(name: str, job_id: str | None = None) -> logging.LoggerAdapter:
    """Return a structured JSON logger bound to an optional job_id."""
    environment = os.environ.get("ENVIRONMENT", "local")
    logger = logging.getLogger(name)

    if not logger.handlers:
        logger.addHandler(_build_handler(environment))
        logger.propagate = False

    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, level_name, logging.INFO))

    extra = {"job_id": job_id} if job_id else {}
    return logging.LoggerAdapter(logger, extra)
