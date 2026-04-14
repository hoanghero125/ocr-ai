"""Core result types that flow through the pipeline. Pure data — no logic, no I/O."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExtractedTable:
    headers: list[str]
    rows: list[list[str]]
    raw: str  # original markdown/HTML for debugging


@dataclass(frozen=True)
class ExtractedField:
    key: str
    label: str
    value: str | None
    confidence: float  # 0.0–1.0


@dataclass(frozen=True)
class PageResult:
    page_number: int
    markdown: str  # raw OCR output from Mistral
    tables: list[ExtractedTable] = field(default_factory=list)
    fields: list[ExtractedField] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True)
class JobProgress:
    total_pages: int
    processed_pages: int
    current_step: str


@dataclass(frozen=True)
class OCRResult:
    job_id: str
    status: str  # matches JobStatus values
    pages: list[PageResult]
    total_pages: int
    processed_pages: int
    errors: list[str]
    metadata: dict
