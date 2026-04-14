"""ContinuationTrigger — async Lambda self-invoke for job continuation."""

import dataclasses
import json
from typing import Any

from src.models.job import JobPayload
from src.shared.exceptions import CheckpointError
from src.shared.logging import get_logger


class ContinuationTrigger:
    def __init__(
        self,
        lambda_client: Any,
        function_name: str,
        max_continuations: int,
    ) -> None:
        """
        Args:
            lambda_client:    boto3 Lambda client
            function_name:    ARN or name of the worker Lambda function
            max_continuations: Hard cap to prevent infinite loops
        """
        self._lambda = lambda_client
        self._function_name = function_name
        self._max_continuations = max_continuations

    async def invoke(self, payload: JobPayload, job_id: str | None = None) -> None:
        """
        Asynchronously invoke the worker Lambda with the updated payload.
        Raises CheckpointError if max_continuations is exceeded.
        """
        import asyncio

        log = get_logger(__name__, job_id=job_id)

        if payload.continuation_count > self._max_continuations:
            raise CheckpointError(
                f"Max continuations ({self._max_continuations}) exceeded for job {payload.job_id}"
            )

        # Serialize payload — same format as SQS message body so worker_handler handles it directly
        body = json.dumps(dataclasses.asdict(payload))

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self._lambda.invoke(
                FunctionName=self._function_name,
                InvocationType="Event",  # fire-and-forget
                Payload=body.encode(),
            ),
        )

        log.info(
            "continuation_triggered",
            extra={"continuation_count": payload.continuation_count},
        )
