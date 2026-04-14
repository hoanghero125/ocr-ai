"""WebhookClient — POST job result to callback_url with retry/backoff."""

import asyncio

import httpx

from src.shared.logging import get_logger

_logger = get_logger(__name__)


class WebhookClient:
    def __init__(self, timeout_s: int, max_retries: int) -> None:
        self._timeout = timeout_s
        self._max_retries = max_retries

    async def send(self, callback_url: str, payload: dict, job_id: str | None = None) -> None:
        """
        POST payload to callback_url.
        Retries on 5xx with exponential backoff.
        Logs and swallows on 4xx or after all retries exhausted — never raises.
        """
        log = get_logger(__name__, job_id=job_id)

        for attempt in range(1, self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(callback_url, json=payload)

                if response.status_code < 400:
                    log.info(
                        "webhook_sent",
                        extra={"status_code": response.status_code, "attempt": attempt},
                    )
                    return

                if 400 <= response.status_code < 500:
                    # Permanent client-side failure — do not retry
                    log.warning(
                        "webhook_failed_4xx",
                        extra={"status_code": response.status_code},
                    )
                    return

                # 5xx — retryable
                log.warning(
                    "webhook_retry",
                    extra={
                        "status_code": response.status_code,
                        "attempt": attempt,
                        "max_retries": self._max_retries,
                    },
                )

            except httpx.RequestError as exc:
                log.warning(
                    "webhook_request_error",
                    extra={"error": str(exc), "attempt": attempt},
                )

            if attempt < self._max_retries:
                await asyncio.sleep(2 ** (attempt - 1))

        log.warning(
            "webhook_failed",
            extra={"attempts": self._max_retries},
        )
