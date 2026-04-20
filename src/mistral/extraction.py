"""ExtractionStage — Stage 2. Parallel field extraction per page with per-page retry."""

import asyncio
import json
import re
import time
from collections.abc import Callable

from src.mistral.client import MistralClient
from src.models.job import FieldInstruction
from src.models.result import ExtractedField, FreeTextBlock, PageResult
from src.shared.logging import get_logger

_logger = get_logger(__name__)

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\r\n]")
_MAX_LABEL_LEN = 200
_MAX_PAGE_TEXT_LEN = 40_000


def sanitize_label(text: str) -> str:
    """Strip control characters and limit to 200 characters."""
    cleaned = _CONTROL_CHAR_RE.sub("", text)
    return cleaned[:_MAX_LABEL_LEN]

_SYSTEM_PROMPT = """You are a Vietnamese document field extractor. Given markdown text from a document, extract and classify ALL visible content.

<confidence_rules>
- Typed/printed text: 0.90–1.0
- Clean handwriting: 0.60–0.89
- Unclear/ambiguous text: 0.30–0.59
- Nearly illegible: 0.01–0.29
</confidence_rules>

<field_type_rules>
- "typed": printed, computer-generated, or stamped text
- "handwritten": written by hand (pen, pencil)
</field_type_rules>

<free_text_rules>
Only classify as free_text if it is a narrative paragraph, letter body, or general note.
Form fields (label:value pairs) must go in extracted_fields.
position: "header" | "body" | "footer" | "signature" — use null if unclear.
</free_text_rules>

<handwritten_percentage>
Estimate percentage of text content that is handwritten (0–100).
Typed documents with no filled fields = 0. Forms with handwritten entries = 20–80.
</handwritten_percentage>"""


def _build_prompt(markdown: str, field_instructions: list[FieldInstruction]) -> str:
    truncated = markdown[:_MAX_PAGE_TEXT_LEN]

    if field_instructions:
        field_list = "\n".join(
            f'- key: "{fi.key}", label: "{fi.label}"'
            + (f', hint: "{fi.description}"' if fi.description else "")
            + (f", min_confidence: {fi.min_confidence}" if fi.min_confidence is not None else "")
            for fi in field_instructions
        )
        fields_section = f"""<specified_fields>
Extract these fields EXACTLY (use the exact key values listed):
{field_list}

Place them in "extracted_fields". Include every listed field even if not found (value="" and confidence=0.0 when absent).
</specified_fields>"""
    else:
        fields_section = """<auto_extraction>
No specific fields requested. Extract ALL important fields automatically.
Put everything in "extracted_fields".
</auto_extraction>"""

    return f"""{fields_section}

<document_text>
{truncated}
</document_text>

Return JSON with exactly these keys:
- extracted_fields: array of {{key, label, value, confidence, field_type}}
- free_texts: array of {{content, confidence, field_type, position}}
- confidence: overall page confidence (0.0–1.0)
- handwritten_percentage: integer 0–100"""


def _parse_field(f: object) -> ExtractedField | None:
    try:
        if not isinstance(f, dict):
            return None
        key = str(f.get("key") or "").strip()
        if not key:
            return None
        label = str(f.get("label") or "").strip()
        value = f.get("value")
        value = str(value) if value is not None else None
        confidence = max(0.0, min(1.0, float(f.get("confidence") or 0.0)))
        field_type = str(f.get("field_type") or "typed")
        return ExtractedField(key=key, label=label, value=value, confidence=confidence, field_type=field_type)
    except Exception:
        return None


def _parse_free_text(f: object) -> FreeTextBlock | None:
    try:
        if not isinstance(f, dict):
            return None
        content = str(f.get("content") or "").strip()
        confidence = max(0.0, min(1.0, float(f.get("confidence") or 0.0)))
        field_type = str(f.get("field_type") or "typed")
        position = f.get("position")
        position = str(position) if position else None
        return FreeTextBlock(content=content, confidence=confidence, field_type=field_type, position=position)
    except Exception:
        return None


def _parse_page_result(
    raw_response: object,
    page: PageResult,
    field_instructions: list[FieldInstruction],
) -> PageResult:
    try:
        content = raw_response.choices[0].message.content
        data = json.loads(content) if isinstance(content, str) else content
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}

    # extracted_fields — apply min_confidence filter
    fi_map = {fi.key: fi for fi in field_instructions}
    raw_extracted = data.get("extracted_fields") or []
    extracted_fields = []
    for f in raw_extracted:
        ef = _parse_field(f)
        if ef is None:
            continue
        fi = fi_map.get(ef.key)
        if fi and fi.min_confidence is not None and ef.confidence < fi.min_confidence:
            ef = ExtractedField(
                key=ef.key, label=ef.label, value=None,
                confidence=ef.confidence, field_type=ef.field_type,
            )
        extracted_fields.append(ef)

    free_texts = [
        ft for ft in (_parse_free_text(f) for f in (data.get("free_texts") or []))
        if ft is not None
    ]

    page_confidence = max(0.0, min(1.0, float(data.get("confidence") or 0.0)))
    handwritten_pct = max(0, min(100, int(data.get("handwritten_percentage") or 0)))

    return PageResult(
        page_number=page.page_number,
        markdown=page.markdown,
        tables=page.tables,
        extracted_fields=extracted_fields,
        free_texts=free_texts,
        handwritten_percentage=handwritten_pct,
        confidence=page_confidence,
        status="success",
    )


class ExtractionStage:
    def __init__(
        self,
        client: MistralClient,
        max_concurrent_pages: int,
        max_retries_per_page: int,
    ) -> None:
        self._client = client
        self._semaphore = asyncio.Semaphore(max_concurrent_pages)
        self._max_retries = max_retries_per_page

    async def run(
        self,
        pages: list[PageResult],
        field_instructions: tuple[FieldInstruction, ...],
        on_page_done: Callable[[int, int], None] | None = None,
        job_id: str | None = None,
    ) -> list[PageResult]:
        log = get_logger(__name__, job_id=job_id)
        fi_list = list(field_instructions)
        total = len(pages)
        t0 = time.monotonic()
        log.info("extraction_start", extra={"page_count": total})

        tasks = [
            self._process_page(page, fi_list, total, on_page_done, log)
            for page in pages
        ]
        updated = await asyncio.gather(*tasks)

        duration_ms = int((time.monotonic() - t0) * 1000)
        log.info("extraction_complete", extra={"duration_ms": duration_ms})
        return list(updated)

    async def _process_page(
        self,
        page: PageResult,
        field_instructions: list[FieldInstruction],
        total: int,
        on_page_done: Callable[[int, int], None] | None,
        log: object,
    ) -> PageResult:
        async with self._semaphore:
            last_error: str | None = None

            for attempt in range(1, self._max_retries + 2):  # +1 base attempt
                try:
                    prompt = _build_prompt(page.markdown, field_instructions)
                    raw = await self._client.chat(
                        messages=[
                            {"role": "system", "content": _SYSTEM_PROMPT},
                            {"role": "user", "content": prompt},
                        ],
                        response_format={"type": "json_object"},
                    )
                    result = _parse_page_result(raw, page, field_instructions)
                    _logger.debug(
                        "extraction_page_complete",
                        extra={
                            "page_num": page.page_number,
                            "extracted": len(result.extracted_fields),
                        },
                    )
                    if on_page_done:
                        on_page_done(page.page_number, total)
                    return result

                except Exception as exc:
                    last_error = str(exc)
                    if attempt <= self._max_retries:
                        _logger.warning(
                            "extraction_page_retry",
                            extra={
                                "page_num": page.page_number,
                                "attempt": attempt,
                                "error": last_error,
                            },
                        )
                        await asyncio.sleep(2 ** (attempt - 1))

            # All retries exhausted — return page with error, do not abort job
            if on_page_done:
                on_page_done(page.page_number, total)
            return PageResult(
                page_number=page.page_number,
                markdown=page.markdown,
                tables=page.tables,
                status="error",
                error_message=last_error,
                error_step="extraction",
            )
