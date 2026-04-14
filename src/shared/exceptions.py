"""Exception hierarchy for OCR AI service."""


class OCRException(Exception):
    """Base exception. All service errors extend this."""

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class ValidationError(OCRException):
    """Input validation failed. Never retryable."""

    def __init__(self, message: str):
        super().__init__(message, retryable=False)


class SSRFBlockedError(ValidationError):
    """URL resolved to a private or internal address."""

    def __init__(self, message: str = "URL is not allowed"):
        super().__init__(message)


class MistralAPIError(OCRException):
    """Mistral API call failed."""

    def __init__(self, message: str, status_code: int = 0):
        retryable = status_code == 429 or status_code >= 500
        super().__init__(message, retryable=retryable)
        self.status_code = status_code


class RateLimitTimeoutError(OCRException):
    """Waited too long for a rate limiter slot."""

    def __init__(self, message: str = "Rate limiter wait timed out"):
        super().__init__(message, retryable=False)


class JobNotFoundError(OCRException):
    """Job ID not found in DynamoDB."""

    def __init__(self, job_id: str):
        super().__init__(f"Job not found: {job_id}", retryable=False)
        self.job_id = job_id


class CheckpointError(OCRException):
    """Checkpoint save or load failed, or max continuations exceeded."""

    def __init__(self, message: str):
        super().__init__(message, retryable=False)
