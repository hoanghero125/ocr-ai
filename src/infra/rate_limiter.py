"""MistralRateLimiter — global RPS cap via DynamoDB atomic counter."""

import asyncio
import time
from typing import Any

from src.shared.exceptions import RateLimitTimeoutError

_POLL_INTERVAL_S = 0.1


class MistralRateLimiter:
    def __init__(
        self,
        table: Any,
        rps: int,
        pk: str,
        ttl_seconds: int,
        max_wait_seconds: int,
    ) -> None:
        """
        Args:
            table:            boto3 DynamoDB Table resource (rate limit table).
                              Pass None to disable (local dev).
            rps:              Max requests per second across all Lambda invocations.
            pk:               Partition key value (e.g. "mistral").
            ttl_seconds:      How long counter items survive in DynamoDB.
            max_wait_seconds: Raise RateLimitTimeoutError if slot not acquired within this time.
        """
        self._table = table
        self._rps = rps
        self._pk = pk
        self._ttl_seconds = ttl_seconds
        self._max_wait_seconds = max_wait_seconds

    @property
    def disabled(self) -> bool:
        return self._table is None

    async def acquire(self) -> None:
        """
        Await until a rate-limit slot is available for the current second.
        No-op when disabled (table is None).
        Raises RateLimitTimeoutError if max_wait_seconds is exceeded.
        """
        if self.disabled:
            return

        deadline = time.monotonic() + self._max_wait_seconds

        while True:
            if time.monotonic() > deadline:
                raise RateLimitTimeoutError(
                    f"Could not acquire Mistral rate limit slot within {self._max_wait_seconds}s"
                )

            current_second = int(time.time())
            count = await self._increment(current_second)

            if count <= self._rps:
                return  # slot acquired

            # Over limit — wait a fraction then retry in the next second
            await asyncio.sleep(_POLL_INTERVAL_S)

    async def _increment(self, second: int) -> int:
        """Atomically increment the counter for `second`. Returns the new count."""
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self._table.update_item(
                Key={"pk": self._pk, "sk": second},
                UpdateExpression="ADD #cnt :one SET #ttl = if_not_exists(#ttl, :ttl_val)",
                ExpressionAttributeNames={"#cnt": "count", "#ttl": "ttl"},
                ExpressionAttributeValues={
                    ":one": 1,
                    ":ttl_val": second + self._ttl_seconds,
                },
                ReturnValues="UPDATED_NEW",
            ),
        )
        return int(response["Attributes"]["count"])
