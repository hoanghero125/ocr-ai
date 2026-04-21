"""WebhookClient — POST job result to callback_url with retry/backoff."""

import asyncio
import hashlib
import hmac
import json

import httpx

from src.shared.logging import get_logger

_logger = get_logger(__name__)


class WebhookClient:
    def __init__(self, timeout_s: int, max_retries: int, secret: str = "") -> None:
        self._timeout = timeout_s
        self._max_retries = max_retries
        self._secret = secret

    async def send(self, callback_url: str, payload: dict, job_id: str | None = None) -> None:
        """
        POST payload to callback_url.
        Retries on 5xx with exponential backoff.
        Logs and swallows on 4xx or after all retries exhausted — never raises.
        If WEBHOOK_SECRET is set, adds X-OCR-Signature: sha256=<hmac> header.
        """
        log = get_logger(__name__, job_id=job_id)

        if self._secret:
            body_bytes = json.dumps(payload, sort_keys=True, default=str).encode()
            sig = hmac.new(self._secret.encode(), body_bytes, hashlib.sha256).hexdigest()
            send_kwargs: dict = {
                "content": body_bytes,
                "headers": {
                    "Content-Type": "application/json",
                    "X-OCR-Signature": f"sha256={sig}",
                },
            }
        else:
            send_kwargs = {"json": payload}

        for attempt in range(1, self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(callback_url, **send_kwargs)

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
