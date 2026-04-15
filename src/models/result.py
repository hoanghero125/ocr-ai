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
    field_type: str = "typed"  # "typed" | "handwritten"


@dataclass(frozen=True)
class FreeTextBlock:
    content: str
    confidence: float
    field_type: str = "typed"
    position: str | None = None  # "header" | "body" | "footer" | "signature" | null


@dataclass(frozen=True)
class PageResult:
    page_number: int
    markdown: str = ""  # raw OCR output from Mistral
    tables: list[ExtractedTable] = field(default_factory=list)
    extracted_fields: list[ExtractedField] = field(default_factory=list)  # user-specified fields
    auto_fields: list[ExtractedField] = field(default_factory=list)       # auto-detected fields
    free_texts: list[FreeTextBlock] = field(default_factory=list)         # narrative text blocks
    handwritten_percentage: int = 0    # 0–100
    confidence: float = 0.0            # page-level extraction confidence
    status: str = "success"            # "success" | "error" | "partial"
    error_message: str | None = None
    error_step: str | None = None


@dataclass(frozen=True)
class JobProgress:
    total_pages: int = 0
    processed_pages: int = 0
    current_step: str = ""


@dataclass(frozen=True)
class OCRResult:
    job_id: str
    status: str  # matches JobStatus values
    pages: list[PageResult]
    total_pages: int
    processed_pages: int
    errors: list[str]
    metadata: dict
    confidence: float = 0.0  # average across all pages
