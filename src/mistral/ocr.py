"""OCRStage — Stage 1 of the pipeline. Calls Mistral OCR and returns typed PageResults."""

from src.mistral.client import MistralClient
from src.mistral.table_parser import parse_tables
from src.models.result import PageResult
from src.shared.logging import get_logger

_logger = get_logger(__name__)


class OCRStage:
    def __init__(self, client: MistralClient) -> None:
        self._client = client

    async def run(self, pdf_url: str, job_id: str | None = None) -> list[PageResult]:
        """
        Call Mistral OCR on pdf_url and return one PageResult per page.
        Each PageResult has markdown + parsed tables. Fields are empty (Stage 2 fills them).
        """
        log = get_logger(__name__, job_id=job_id)
        log.info("ocr_stage_start")

        import time
        t0 = time.monotonic()

        response = await self._client.ocr(pdf_url)

        pages: list[PageResult] = []
        raw_pages = response.pages if hasattr(response, "pages") else response.get("pages", [])

        for page in raw_pages:
            if hasattr(page, "markdown"):
                page_num = page.index + 1
                markdown = page.markdown or ""
            else:
                page_num = page.get("index", 0) + 1
                markdown = page.get("markdown") or ""

            tables = parse_tables(markdown)
            pages.append(PageResult(
                page_number=page_num,
                markdown=markdown,
                tables=tables,
                fields=[],
                error=None,
            ))

        duration_ms = int((time.monotonic() - t0) * 1000)
        log.info(
            "ocr_stage_complete",
            extra={"page_count": len(pages), "duration_ms": duration_ms},
        )
        return pages
