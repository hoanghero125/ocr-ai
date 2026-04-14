"""Thin async wrapper over the Mistral SDK. ocr() and chat() only."""

import asyncio
import base64
import time
from typing import Any

import httpx
from mistralai.client import Mistral

from src.infra.rate_limiter import MistralRateLimiter
from src.shared.exceptions import MistralAPIError
from src.shared.logging import get_logger

_logger = get_logger(__name__)


def _is_http(url: str) -> bool:
    return url.startswith("http://")


async def _to_data_uri(url: str, timeout_s: int) -> str:
    """Download a plain-http PDF and return a base64 data URI."""
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        response = await client.get(url)
        response.raise_for_status()
    encoded = base64.b64encode(response.content).decode()
    return f"data:application/pdf;base64,{encoded}"


class MistralClient:
    def __init__(
        self,
        api_key: str,
        ocr_model: str,
        chat_model: str,
        table_format: str,
        base_url: str,
        timeout_s: int,
        max_retries: int,
        rate_limiter: MistralRateLimiter | None = None,
    ) -> None:
        self._sdk = Mistral(api_key=api_key, server_url=base_url)
        self._ocr_model = ocr_model
        self._chat_model = chat_model
        self._table_format = table_format
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._rate_limiter = rate_limiter

    async def _acquire(self) -> None:
        if self._rate_limiter:
            await self._rate_limiter.acquire()

    async def ocr(self, pdf_url: str) -> dict:
        """
        Call the Mistral OCR API on a PDF URL.
        http:// URLs are downloaded and converted to base64 data URIs first.
        Returns the raw API response dict.
        """
        await self._acquire()

        if _is_http(pdf_url):
            document_url = await _to_data_uri(pdf_url, self._timeout_s)
        else:
            document_url = pdf_url

        return await self._call_with_retry(
            lambda: self._sdk.ocr.process(
                model=self._ocr_model,
                document={"type": "document_url", "document_url": document_url},
                include_image_base64=False,
            )
        )

    async def chat(
        self,
        messages: list[dict],
        response_format: dict | None = None,
    ) -> dict:
        """
        Call the Mistral chat completions API.
        Returns the raw API response dict.
        """
        await self._acquire()

        kwargs: dict[str, Any] = {
            "model": self._chat_model,
            "messages": messages,
        }
        if response_format:
            kwargs["response_format"] = response_format

        return await self._call_with_retry(lambda: self._sdk.chat.complete(**kwargs))

    async def _call_with_retry(self, fn: Any) -> Any:
        last_exc: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                return await asyncio.to_thread(fn)
            except Exception as exc:
                status = getattr(exc, "status_code", 0) or 0
                retryable = status == 429 or status >= 500
                last_exc = MistralAPIError(str(exc), status_code=status)

                if not retryable or attempt == self._max_retries:
                    raise last_exc

                wait = 2 ** (attempt - 1)
                _logger.debug(
                    "mistral_retry",
                    extra={"attempt": attempt, "status_code": status, "wait_s": wait},
                )
                await asyncio.sleep(wait)

        raise last_exc  # type: ignore[misc]
