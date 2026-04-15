"""Pydantic validation schemas for API request and response bodies."""

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, field_validator, model_validator

_KEY_PATTERN = re.compile(r"^[a-zA-Z0-9_]+$")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
_MAX_FIELD_INSTRUCTIONS = 50
_MAX_KEY_LEN = 50
_MAX_LABEL_LEN = 200
_MAX_DESCRIPTION_LEN = 500


class FieldInstructionSchema(BaseModel):
    key: str
    label: str
    description: str = ""
    min_confidence: float | None = None

    @field_validator("key")
    @classmethod
    def key_must_be_alphanumeric(cls, v: str) -> str:
        if not _KEY_PATTERN.match(v):
            raise ValueError("key must match ^[a-zA-Z0-9_]+$")
        if len(v) > _MAX_KEY_LEN:
            raise ValueError(f"key must not exceed {_MAX_KEY_LEN} characters")
        return v

    @field_validator("label")
    @classmethod
    def label_must_be_clean(cls, v: str) -> str:
        if _CONTROL_CHAR_RE.search(v):
            raise ValueError("label must not contain control characters")
        if len(v) > _MAX_LABEL_LEN:
            raise ValueError(f"label must not exceed {_MAX_LABEL_LEN} characters")
        return v

    @field_validator("description")
    @classmethod
    def description_must_be_clean(cls, v: str) -> str:
        if _CONTROL_CHAR_RE.search(v):
            raise ValueError("description must not contain control characters")
        if len(v) > _MAX_DESCRIPTION_LEN:
            raise ValueError(f"description must not exceed {_MAX_DESCRIPTION_LEN} characters")
        return v

    @field_validator("min_confidence")
    @classmethod
    def confidence_range(cls, v: float | None) -> float | None:
        if v is not None and not (0.0 <= v <= 1.0):
            raise ValueError("min_confidence must be between 0.0 and 1.0")
        return v


class ProcessOptions(BaseModel):
    output_format: str = "structured"
    include_confidence: bool = True
    language_hints: list[str] = ["vi", "en"]


class JobMetadata(BaseModel):
    client_id: str | None = None
    document_id: str | None = None
    extra: dict[str, Any] | None = None


class ProcessRequest(BaseModel):
    pdf_url: str
    callback_url: str | None = None
    options: ProcessOptions = ProcessOptions()
    field_instructions: list[FieldInstructionSchema] = []
    metadata: JobMetadata | None = None

    @field_validator("pdf_url")
    @classmethod
    def pdf_url_must_be_http(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("pdf_url must be an http or https URL")
        return v

    @field_validator("callback_url")
    @classmethod
    def callback_url_must_be_https(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith("https://"):
            raise ValueError("callback_url must be an https URL")
        return v

    @model_validator(mode="after")
    def check_field_instructions(self) -> "ProcessRequest":
        if len(self.field_instructions) > _MAX_FIELD_INSTRUCTIONS:
            raise ValueError(
                f"field_instructions may not exceed {_MAX_FIELD_INSTRUCTIONS} items"
            )
        keys = [fi.key for fi in self.field_instructions]
        if len(keys) != len(set(keys)):
            raise ValueError("field_instructions must have unique keys")
        return self


class ProcessResponse(BaseModel):
    job_id: str
    status: str
    status_url: str
    created_at: datetime
    message: str = "Job queued successfully"


class ProgressSchema(BaseModel):
    total_pages: int
    processed_pages: int
    current_step: str


class StatusResponse(BaseModel):
    job_id: str
    status: str
    progress: ProgressSchema | None = None
    result_url: str | None = None
    error: str | None = None
    created_at: str
    updated_at: str
