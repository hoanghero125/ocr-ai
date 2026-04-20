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
    extracted_fields: list[ExtractedField] = field(default_factory=list)
    pages_markdown: list[str] = field(default_factory=list)  # raw markdown per page (full OCR output)


def aggregate_extracted_fields(pages: list[PageResult]) -> list[ExtractedField]:
    """
    Merge extracted fields across all pages.

    Group key = (key, label). For duplicate groups, keep the entry with the longest
    non-empty value; if value lengths tie, keep the one with higher confidence.
    """
    merged: dict[tuple[str, str], ExtractedField] = {}
    ordered_keys: list[tuple[str, str]] = []

    for page in pages:
        for field in page.extracted_fields:
            group_key = (field.key, field.label)
            if group_key not in merged:
                merged[group_key] = field
                ordered_keys.append(group_key)
                continue

            current = merged[group_key]
            current_value_len = len((current.value or "").strip())
            candidate_value_len = len((field.value or "").strip())

            if candidate_value_len > current_value_len:
                merged[group_key] = field
            elif candidate_value_len == current_value_len and field.confidence > current.confidence:
                merged[group_key] = field

    return [merged[k] for k in ordered_keys]
