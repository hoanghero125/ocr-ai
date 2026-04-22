# OCR AI Service

PDF OCR and structured field extraction service built on Mistral AI. Accepts a PDF URL, runs multi-page OCR, and optionally extracts structured fields — deployed as two AWS Lambda functions behind API Gateway, with SQS for async job queuing.

**API docs:** https://ocr-ai.digeni.vn/docs

## Architecture

```
Client
  │
  ▼
API Gateway (HTTP API) — https://ocr-ai.digeni.vn
  │
  ▼
API Lambda  ──►  DynamoDB (job state)
  │
  ▼
SQS Queue
  │
  ▼
Worker Lambda
  │
  ├──► Mistral OCR API  (PDF → markdown per page)
  ├──► Mistral Chat API (markdown → structured fields)
  ├──► MinIO            (checkpoints + final result JSON)
  └──► Webhook          (optional callback_url notification)
```

Both Lambda functions share a single ECR Docker image, differentiated by `image_config.command`:
- **API** → `src.lambda_handler.api_gateway_handler` (60s timeout, 512 MB)
- **Worker** → `src.lambda_handler.worker_handler` (900s timeout, 2048 MB)

For jobs that exceed the 15-minute Lambda limit, the worker self-invokes with a continuation payload and MinIO checkpoints, picking up exactly where it left off.

## Local Development

The local dev server uses real infrastructure — DynamoDB, MinIO, and Mistral — with SQS replaced by inline background processing.

### Setup (Docker Compose — recommended)

Edit **`docker-compose.yml`** defaults, or copy **`docker-compose.env.example`** to **`.env`** in the project root and set `MISTRAL_API_KEY`, `AWS_ACCESS_KEY_ID`, and `AWS_SECRET_ACCESS_KEY` (Compose reads `.env` only for `${VAR}` substitution — do not commit it). Then:

```bash
docker compose up --build
```

Open **http://localhost:8000/docs** for the interactive Swagger UI.

### Setup (Python on the host)

```bash
pip install -r requirements.txt
export MISTRAL_API_KEY=...   # plus other vars as in docker-compose.yml
python scripts/local_server.py
```

### Endpoints (local + production)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/process` | Submit a PDF for async processing — returns `job_id` immediately |
| `GET` | `/jobs/{job_id}` | Poll job status and get `result_url` when done |
| `POST` | `/jobs/{job_id}/refine` | Re-extract specific fields with a correction hint — no re-OCR |
| `POST` | `/extract` | Synchronous OCR + extraction — returns raw data immediately (no queue) |
| `GET` | `/health` | Health check |
| `GET` | `/docs` | Swagger UI |

## Production API

### Submit a job

```
POST /process
Content-Type: application/json
```

```json
{
  "pdf_url": "https://example.com/document.pdf",
  "callback_url": "https://your-app.com/webhook",
  "field_instructions": [
    { "key": "ho_ten",    "label": "Ho va ten" },
    { "key": "ngay_sinh", "label": "Ngay sinh", "min_confidence": 0.8 },
    { "key": "so_cmnd",   "label": "So CMND / CCCD", "description": "Trich xuat so CMND hoac CCCD" }
  ],
  "options": {
    "language_hints": ["vi", "en"]
  },
  "metadata": {
    "client_id": "ocr-core-backend",
    "document_id": "2303",
    "extra": { "file_id": 4102 }
  }
}
```

Response `202`:
```json
{
  "code": 0,
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "queued",
  "status_url": "https://ocr-ai.digeni.vn/jobs/550e8400-...",
  "created_at": "2025-01-01T00:00:00+00:00",
  "message": "Job queued successfully"
}
```

**Field instruction fields:**
- `key` — alphanumeric + underscores, max 50 chars (must be unique per request)
- `label` — human-readable name shown to the model, max 200 chars
- `description` _(optional)_ — extra context for the model, max 500 chars
- `min_confidence` _(optional)_ — threshold 0.0–1.0; extracted values below this are returned as `null`
- `dataType` _(optional)_ — instructs the model how to format the extracted value: `TEXT` (plain string), `NUMBER` (numeric value only, no units), `DATE` (format `dd/MM/yyyy`)

### Poll job status

```
GET /jobs/{job_id}
```

Success response:
```json
{
  "code": 0,
  "job_id": "550e8400-...",
  "status": "completed",
  "result_url": "https://minioapi.digeni.vn/mistral-ai/results/550e8400-.../result.json",
  "progress": { "total_pages": 3, "processed_pages": 3, "current_step": "Processing complete" },
  "error_code": null,
  "error": null,
  "created_at": "2025-01-01T00:00:00+00:00",
  "updated_at": "2025-01-01T00:01:30+00:00"
}
```

Failed response:
```json
{
  "code": 6001,
  "job_id": "550e8400-...",
  "status": "failed",
  "result_url": null,
  "progress": null,
  "error_code": 6001,
  "error": "MistralAPIError: upstream timeout after 120s",
  "created_at": "2025-01-01T00:00:00+00:00",
  "updated_at": "2025-01-01T00:01:30+00:00"
}
```

**Job statuses:** `queued` → `processing` → `completed` / `completed_with_errors` / `failed`

### Webhook payload (on completion)

If `callback_url` was provided, the worker POSTs this to it when the job reaches a terminal state:

```json
{
  "job_id": "550e8400-...",
  "status": "completed",
  "result_url": "https://minioapi.digeni.vn/mistral-ai/results/550e8400-.../result.json",
  "errors": [],
  "metadata": { "client_id": "ocr-core-backend", "document_id": "2303" }
}
```

Retries 3× with exponential backoff on 5xx. Permanent failure on 4xx. `callback_url` must be `https://`.

### Re-extract with a correction hint

If a field comes back `null` (confidence below `min_confidence`) or with a wrong value, call `/refine` to steer the model without re-running OCR:

```
POST /jobs/{job_id}/refine
Content-Type: application/json
```

```json
{
  "field_instructions": [
    {
      "key": "ngay_sinh",
      "label": "Ngay sinh",
      "description": "Birth date in top-right corner, format DD/MM/YYYY",
      "min_confidence": 0.6
    }
  ]
}
```

- Only the fields listed are re-extracted — other fields in the original result are untouched
- The stored result at `result_url` is updated in-place with the refined values
- Job must be in `completed` or `completed_with_errors` status

Response `200`:
```json
{
  "code": 0,
  "job_id": "550e8400-...",
  "refined_fields": [
    { "key": "ngay_sinh", "label": "Ngay sinh", "value": "15/03/1990", "confidence": 0.88, "field_type": "typed" }
  ],
  "pages_reprocessed": 3
}
```

### Result JSON structure

The object at `result_url` (and the response from `POST /extract`) follows this shape:

```json
{
  "job_id": "...",
  "status": "completed",
  "total_pages": 3,
  "confidence": 0.933,
  "pages": [
    {
      "page_number": 1,
      "handwritten_percentage": 5,
      "extracted_fields": [
        { "key": "ho_ten", "label": "Ho va ten", "value": "Nguyen Van A", "confidence": 0.95, "field_type": "typed" }
      ],
      "auto_fields": [
        { "key": "quoc_hieu", "label": "Quoc hieu", "value": "CONG HOA XA HOI CHU NGHIA VIET NAM", "confidence": 0.95, "field_type": "typed" }
      ],
      "tables": [
        { "headers": ["Col A", "Col B"], "rows": [["val1", "val2"]] }
      ],
      "free_texts": [
        { "content": "Paragraph text...", "confidence": 0.9, "field_type": "typed", "position": "body" }
      ],
      "confidence": 0.93,
      "status": "success",
      "error_message": null,
      "error_step": null
    }
  ]
}
```

- `extracted_fields` — fields from your `field_instructions` (one entry per field, `value: null` if below `min_confidence`)
- `auto_fields` — other important fields detected automatically
- `free_texts` — narrative paragraphs/notes; `position`: `header` | `body` | `footer` | `signature`
- `field_type`: `"typed"` (printed/stamped) or `"handwritten"`

## Infrastructure (Terraform)

```bash
cd terraform

terraform init
terraform apply \
  -var="environment=staging" \
  -var="mistral_api_key=sk-..." \
  -var="ecr_image_uri=123456789.dkr.ecr.ap-southeast-1.amazonaws.com/bizgenie-ocr:latest" \
  -var="minio_access_key=..." \
  -var="minio_secret_key=..."
```

### Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `environment` | yes | — | `staging` or `production` |
| `mistral_api_key` | yes | — | Mistral API key |
| `ecr_image_uri` | yes | — | ECR image URI |
| `minio_access_key` | yes | — | MinIO access key |
| `minio_secret_key` | yes | — | MinIO secret key |
| `aws_region` | no | `us-east-1` | AWS region |
| `minio_url` | no | `https://minioapi.digeni.vn` | MinIO endpoint URL |
| `minio_bucket` | no | `mistral-ai` | MinIO bucket for results and checkpoints |
| `http_api_base_url` | no | `https://ocr-ai.digeni.vn` | Public API URL (used in `status_url` responses) |
| `api_token` | no | `""` | Bearer token for API auth (empty = disabled) |

### AWS resources created

- **API Gateway** — HTTP API with CORS, throttling (50 RPS / 100 burst), access logs
- **Lambda** — API (60s) and Worker (900s) functions from shared ECR image
- **SQS** — main queue + dead-letter queue (after 3 receive attempts)
- **DynamoDB** — jobs table (TTL) + rate-limiter table
- **CloudWatch** — log groups for API Gateway and both Lambda functions (30d retention)
- **IAM** — least-privilege roles per function

### Build and push Docker image

```bash
# Authenticate
aws ecr get-login-password --region ap-southeast-1 | \
  docker login --username AWS --password-stdin 123456789.dkr.ecr.ap-southeast-1.amazonaws.com

# Build and push
docker build -t bizgenie-ocr:latest .
docker tag bizgenie-ocr:latest 123456789.dkr.ecr.ap-southeast-1.amazonaws.com/bizgenie-ocr:latest
docker push 123456789.dkr.ecr.ap-southeast-1.amazonaws.com/bizgenie-ocr:latest
```

## Configuration

Local dev with Docker reads variables from **`docker-compose.yml`** (Compose can also load a sibling `.env` file for `${VAR}` substitution only). On AWS Lambda, the same variables are set by Terraform.

| Variable | Default | Description |
|----------|---------|-------------|
| `MISTRAL_API_KEY` | — | Required |
| `MISTRAL_OCR_MODEL` | `mistral-ocr-latest` | OCR model |
| `MISTRAL_CHAT_MODEL` | `mistral-small-latest` | Extraction model |
| `MISTRAL_TABLE_FORMAT` | `html` | Table output format (`html` or `markdown`) |
| `MISTRAL_TIMEOUT_S` | `120` | Per-request timeout (seconds) |
| `MISTRAL_MAX_RETRIES` | `4` | Retry attempts on transient errors |
| `MAX_CONCURRENT_PAGES` | `4` | Parallel extraction semaphore |
| `EXTRACT_MAX_RETRIES_PER_PAGE` | `2` | Per-page extraction retries |
| `MAX_CONTINUATIONS` | `5` | Max Lambda self-invocations per job |
| `LAMBDA_EXTRACT_CONTINUATION_ENABLED` | `false` | Enable timeout-based continuation |
| `WEBHOOK_TIMEOUT_S` | `10` | Webhook HTTP timeout |
| `WEBHOOK_MAX_RETRIES` | `3` | Webhook retry attempts |
| `WEBHOOK_SECRET` | `""` | HMAC secret for webhook signature — if set, adds `X-OCR-Signature: sha256=<hmac>` header so receivers can verify authenticity (empty = disabled) |
| `HTTP_API_BASE_URL` | `https://ocr-ai.digeni.vn` | Public API base URL |
| `WORKER_FUNCTION_NAME` | `""` | Worker Lambda name for self-invocation |
| `API_TOKEN` | `""` | Bearer token for API auth (empty = disabled) |
| `MISTRAL_RATE_LIMIT_TABLE` | `""` | DynamoDB table for rate limiting (empty = disabled) |

## Testing

```bash
python -m pytest tests/ --cov=src --cov-report=term-missing -q
```

Test layout:
- `tests/unit/` — pure unit tests, no AWS, all dependencies mocked
- `tests/integration/` — moto-based tests that exercise real DynamoDB/S3/SQS/Lambda logic

## Project Structure

```
src/
├── api/
│   ├── routes.py          # API Gateway event router + OpenAPI spec builder
│   └── schemas.py         # Pydantic request/response validation
├── checkpoint/
│   └── manager.py         # MinIO checkpoint save/load with idempotent DynamoDB writes
├── infra/
│   ├── rate_limiter.py    # DynamoDB-backed Mistral rate limiter
│   ├── repository.py      # DynamoDB job CRUD
│   ├── store.py           # MinIO result/checkpoint storage
│   └── webhook.py         # Callback delivery with retry/backoff
├── mistral/
│   ├── client.py          # Mistral SDK wrapper (OCR + chat)
│   ├── extraction.py      # Stage 2: parallel field extraction per page
│   ├── ocr.py             # Stage 1: PDF → markdown per page
│   └── table_parser.py    # Markdown table → structured rows
├── models/
│   ├── job.py             # JobPayload, FieldInstruction, JobStatus
│   └── result.py          # PageResult, ExtractedField, OCRResult (frozen dataclasses)
├── pipeline/
│   ├── continuation.py    # Lambda self-invocation trigger
│   └── processor.py       # Orchestrates OCR → extraction → checkpoint → webhook
├── shared/
│   ├── codes.py           # Numeric response/error codes (single source of truth)
│   ├── config.py          # Settings loaded from env vars (lru_cached)
│   ├── exceptions.py      # Typed exceptions (JobNotFoundError, SSRFBlockedError, …)
│   ├── logging.py         # Structured JSON logger with secret redaction
│   └── url_validator.py   # Async SSRF protection (DNS resolution + private IP block)
├── workers/
│   └── sqs.py             # SQS batch handler + direct invocation handler
├── container.py           # Dependency wiring (singleton components)
└── lambda_handler.py      # Lambda entry points
scripts/
└── local_server.py        # FastAPI dev server (real DynamoDB + MinIO + Mistral, no SQS)
terraform/                 # All infrastructure-as-code
```

## Response Codes

Every response includes a top-level `code` field. `code = 0` means success. Any non-zero value is an error.

### API errors — returned directly in the response body

| `code` | HTTP | Description |
|--------|------|-------------|
| `0` | — | Success |
| `1001` | 400 | VALIDATION_ERROR — Request body failed schema validation (missing `pdf_url`, invalid key, etc.) |
| `1002` | 400 | INVALID_JSON — Request body is not valid JSON |
| `1003` | 400 | INVALID_URL — `pdf_url` or `callback_url` is not a valid URL |
| `1004` | 400 | URL_NOT_ALLOWED — URL resolves to a private/internal address (SSRF protection) |
| `2001` | 401 | UNAUTHORIZED — Missing or invalid `Authorization: Bearer <token>` header |
| `3001` | 404 | JOB_NOT_FOUND — No job exists with the given `job_id` |
| `3002` | 404 | NOT_FOUND — Route does not exist |
| `5001` | 503 | DATABASE_ERROR — DynamoDB unavailable, safe to retry |
| `5002` | 503 | QUEUE_ERROR — SQS unavailable, safe to retry |
| `5003` | 500 | INTERNAL_ERROR — Unexpected server error |

Error response shape:
```json
{ "code": 2001, "message": "Missing or invalid Bearer token" }
```

### Job pipeline errors — in `GET /jobs/{job_id}` when `status = "failed"`

When a job fails, the top-level `code` equals `error_code` and `error` contains the detail message.

| `code` / `error_code` | Description |
|----------------------|-------------|
| `6001` | OCR_FAILED — Mistral OCR API call failed (timeout, 5xx, etc.) |
| `6002` | RATE_LIMIT_ERROR — Waited too long for a Mistral rate limit slot |
| `6003` | CHECKPOINT_ERROR — Checkpoint save/load failed or max continuations exceeded |
| `6004` | JOB_INTERNAL_ERROR — Unexpected error during job processing |

## Authentication

All endpoints except `/health`, `/docs`, and `/openapi.json` require a Bearer token:

```
Authorization: Bearer <token>
```

Returns `401 Unauthorized` if the header is missing or the token is wrong. Auth is disabled when `API_TOKEN` is empty (local dev default).

Set the token via the `API_TOKEN` env var (or `var.api_token` in Terraform).

## Security Notes

- **SSRF protection** — `pdf_url` and `callback_url` are validated: async DNS resolution blocks private/loopback IP ranges (RFC-1918, link-local, AWS IMDS)
- **Input validation** — field keys, labels, and descriptions are length-limited and control-character-stripped; max 50 field instructions per request; duplicate keys rejected
- **Secret redaction** — structured logger strips `api_key`, `token`, `password`, `authorization`, and `callback_url` from all log output
- **Idempotent writes** — DynamoDB conditional expressions prevent duplicate checkpoint writes on replayed SQS messages
- **Least-privilege IAM** — API and Worker roles have separate, scoped policies
