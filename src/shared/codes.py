"""Numeric response codes used across API responses and job status."""

# ── Success ───────────────────────────────────────────────────────────────────
SUCCESS = 0

# ── Client / input errors (1xxx) ─────────────────────────────────────────────
VALIDATION_ERROR = 1001   # Request body failed schema validation
INVALID_JSON     = 1002   # Request body is not valid JSON
INVALID_URL      = 1003   # pdf_url / callback_url is not a valid URL
URL_NOT_ALLOWED  = 1004   # URL resolves to a private/internal address (SSRF)

# ── Auth errors (2xxx) ────────────────────────────────────────────────────────
UNAUTHORIZED = 2001       # Missing or invalid Bearer token

# ── Not found (3xxx) ─────────────────────────────────────────────────────────
JOB_NOT_FOUND = 3001      # No job with the given job_id
NOT_FOUND     = 3002      # Route does not exist

# ── Server / infrastructure errors (5xxx) ────────────────────────────────────
DATABASE_ERROR = 5001     # DynamoDB unavailable — safe to retry
QUEUE_ERROR    = 5002     # SQS unavailable — safe to retry
INTERNAL_ERROR = 5003     # Unexpected server error

# ── Job pipeline errors (6xxx) ───────────────────────────────────────────────
OCR_FAILED        = 6001  # Mistral OCR API call failed
RATE_LIMIT_ERROR  = 6002  # Mistral rate limit wait timed out
CHECKPOINT_ERROR  = 6003  # Checkpoint save/load failed or max continuations exceeded
JOB_INTERNAL_ERROR = 6004 # Unexpected error during job processing
