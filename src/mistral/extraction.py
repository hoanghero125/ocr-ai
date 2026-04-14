"""ExtractionStage — Stage 2. Parallel field extraction per page with per-page retry."""

import asyncio
import json
import re
import time
from collections.abc import Callable

from src.mistral.client import MistralClient
from src.models.job import FieldInstruction
from src.models.result import ExtractedField, PageResult
from src.shared.logging import get_logger

_logger = get_logger(__name__)

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\r\n]")
_MAX_LABEL_LEN = 200
_MAX_PAGE_TEXT_LEN = 40_000


def sanitize_label(text: str) -> str:
    """Strip control characters and limit to 200 characters."""
    cleaned = _CONTROL_CHAR_RE.sub("", text)
    return cleaned[:_MAX_LABEL_LEN]


def _build_prompt(markdown: str, field_instructions: list[FieldInstruction]) -> str:
    fields_desc = "\n".join(
        f"- {sanitize_label(fi.key)}: {sanitize_label(fi.label)}"
        + (f" — {sanitize_label(fi.description)}" if fi.description else "")
        for fi in field_instructions
    )
    truncated = markdown[:_MAX_PAGE_TEXT_LEN]
    return (
        f"Extract the following fields from the document text below.\n\n"
        f"Fields to extract:\n{fields_desc}\n\n"
        f"Document text:\n{truncated}\n\n"
        f"Return a JSON object where each key maps to "
        f'{{\"value\": string|null, \"confidence\": float between 0.0 and 1.0}}.'
    )


def _build_response_format(field_instructions: list[FieldInstruction]) -> dict:
    properties = {
        fi.key: {
            "type": "object",
            "properties": {
                "value": {"type": ["string", "null"]},
                "confidence": {"type": "number"},
            },
            "required": ["value", "confidence"],
        }
        for fi in field_instructions
    }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "field_extraction",
            "schema": {
                "type": "object",
                "properties": properties,
                "required": [fi.key for fi in field_instructions],
            },
            "strict": True,
        },
    }


def _parse_fields(
    raw_response: dict,
    field_instructions: list[FieldInstruction],
) -> list[ExtractedField]:
    try:
        content = raw_response.choices[0].message.content
        data = json.loads(content) if isinstance(content, str) else content
    except Exception:
        data = {}

    fields: list[ExtractedField] = []
    for fi in field_instructions:
        entry = data.get(fi.key) or {}
        value = entry.get("value") if isinstance(entry, dict) else None
        confidence = float(entry.get("confidence", 0.0)) if isinstance(entry, dict) else 0.0
        confidence = max(0.0, min(1.0, confidence))

        if fi.min_confidence is not None and confidence < fi.min_confidence:
            value = None

        fields.append(ExtractedField(
            key=fi.key,
            label=fi.label,
            value=value,
            confidence=confidence,
        ))
    return fields


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
        """
        Extract structured fields from each page in parallel.
        Returns the same pages with fields populated.
        on_page_done(page_num, total) called after each page completes.
        """
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
                    response_format = _build_response_format(field_instructions)
                    raw = await self._client.chat(
                        messages=[{"role": "user", "content": prompt}],
                        response_format=response_format,
                    )
                    fields = _parse_fields(raw, field_instructions)
                    result = PageResult(
                        page_number=page.page_number,
                        markdown=page.markdown,
                        tables=page.tables,
                        fields=fields,
                        error=None,
                    )
                    _logger.debug(
                        "extraction_page_complete",
                        extra={"page_num": page.page_number, "field_count": len(fields)},
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
                fields=[],
                error=last_error,
            )
