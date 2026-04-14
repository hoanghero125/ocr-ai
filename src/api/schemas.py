"""Pydantic validation schemas for API request and response bodies."""

import re
from typing import Any

from pydantic import BaseModel, field_validator, model_validator

_KEY_PATTERN = re.compile(r"^[a-zA-Z0-9_]+$")
_MAX_FIELD_INSTRUCTIONS = 50


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
    language_hints: list[str] = []


class ProcessRequest(BaseModel):
    pdf_url: str
    callback_url: str | None = None
    options: ProcessOptions = ProcessOptions()
    field_instructions: list[FieldInstructionSchema] = []
    metadata: dict[str, Any] = {}

    @model_validator(mode="after")
    def check_field_instructions_limit(self) -> "ProcessRequest":
        if len(self.field_instructions) > _MAX_FIELD_INSTRUCTIONS:
            raise ValueError(
                f"field_instructions may not exceed {_MAX_FIELD_INSTRUCTIONS} items"
            )
        return self


class ProcessResponse(BaseModel):
    job_id: str
    status: str
    status_url: str


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
