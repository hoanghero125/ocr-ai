"""Job types: JobStatus, FieldInstruction, JobPayload. Pure data — no logic, no I/O."""

from dataclasses import dataclass, field
from enum import Enum


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"


@dataclass(frozen=True)
class FieldInstruction:
    key: str
    label: str
    description: str = ""
    min_confidence: float | None = None
    data_type: str | None = None  # "TEXT" | "NUMBER" | "DATE"


@dataclass(frozen=True)
class JobPayload:
    job_id: str
    pdf_url: str
    callback_url: str | None = None
    field_instructions: tuple[FieldInstruction, ...] = field(default_factory=tuple)
    options: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    continuation_count: int = 0
    ocr_checkpoint_key: str | None = None
    extraction_checkpoint_key: str | None = None
