# OCR AI Service

PDF OCR and structured field extraction service built on Mistral AI. Accepts a PDF URL, runs multi-page OCR, and optionally extracts structured fields — deployed as two AWS Lambda functions behind API Gateway, with SQS for async job queuing.

## Architecture

```
Client
  │
  ▼
API Gateway (HTTP API)
  │
  ▼
API Lambda  ──►  DynamoDB (job state)
  │                  │
  ▼                  │
SQS Queue            │
  │                  │
  ▼                  │
Worker Lambda ◄──────┘
  │
  ├──► Mistral OCR API  (page → markdown)
  ├──► Mistral Chat API (markdown → structured fields)
  ├──► S3 (checkpoints + final result JSON)
  └──► Webhook (optional callback_url notification)
```

Both Lambda functions share a single ECR Docker image, differentiated by `image_config.command`:
- **API** → `src.lambda_handler.api_gateway_handler` (60s timeout, 512 MB)
- **Worker** → `src.lambda_handler.worker_handler` (900s timeout, 2048 MB)

For jobs that exceed the 15-minute Lambda limit, the worker self-invokes with a continuation payload and S3 checkpoints, picking up exactly where it left off.

## Local Development

No AWS required. The local server wires Mistral directly to FastAPI — no SQS, no DynamoDB, no S3.

### Setup

```bash
# 1. Install dev dependencies
pip install -r requirements-dev.txt

# 2. Copy and configure env
cp .env.example .env
# Set MISTRAL_API_KEY in .env

# 3. Start the server
python scripts/local_server.py
```

Open **http://localhost:8000/docs** for the interactive Swagger UI.

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/ocr` | Upload a PDF, get raw markdown per page |
| `POST` | `/extract` | Upload a PDF + field definitions, get structured values per page |
| `GET` | `/health` | Health check |

#### `/extract` — field definitions format

Pass `fields` as a JSON array in the form body:

```json
[
  { "key": "ho_ten",     "label": "Ho va ten" },
  { "key": "ngay_sinh",  "label": "Ngay sinh" },
  { "key": "dia_chi",    "label": "Dia chi thuong tru" },
  { "key": "so_cmnd",    "label": "So CMND / CCCD", "min_confidence": 0.8 }
]
```

Each field:
- `key` — alphanumeric + underscores, max 50 chars
- `label` — human-readable name shown to the model, max 200 chars
- `description` _(optional)_ — extra context for the model, max 500 chars
- `min_confidence` _(optional)_ — threshold 0.0–1.0; values below this are returned as `null`

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
    { "key": "ho_ten", "label": "Ho va ten" }
  ],
  "metadata": { "ref": "invoice-123" }
}
```

Response `202`:
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "queued",
  "status_url": "https://api.example.com/jobs/550e8400-..."
}
```

### Poll job status

```
GET /jobs/{job_id}
```

```json
{
  "job_id": "550e8400-...",
  "status": "completed",
  "result_url": "https://bucket.s3.amazonaws.com/results/...",
  "progress": { "total_pages": 10, "processed_pages": 10, "current_step": "done" },
  "created_at": "2025-01-01T00:00:00+00:00",
  "updated_at": "2025-01-01T00:01:30+00:00"
}
```

**Job statuses:** `queued` → `processing` → `completed` / `failed`

### Webhook payload (on completion)

If `callback_url` was provided, the worker POSTs this to it:

```json
{
  "job_id": "550e8400-...",
  "status": "completed",
  "result_url": "https://...",
  "total_pages": 10
}
```

Retries 3× with exponential backoff on 5xx. Permanent failure on 4xx. `callback_url` must be `https://`.

## Infrastructure (Terraform)

```bash
cd terraform

# First deploy — build and push the Docker image first
terraform init
terraform apply \
  -var="environment=staging" \
  -var="mistral_api_key=sk-..." \
  -var="ecr_image_uri=123456789.dkr.ecr.us-east-1.amazonaws.com/bizgenie-ocr:latest"
```

### Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `environment` | yes | — | `staging` or `production` |
| `mistral_api_key` | yes | — | Mistral API key (stored as Lambda env var) |
| `ecr_image_uri` | yes | — | ECR image URI pushed before `terraform apply` |
| `aws_region` | no | `us-east-1` | AWS region |
| `results_base_url` | no | `""` | CloudFront domain for result file URLs |

### AWS resources created

- **API Gateway** — HTTP API with throttling (50 RPS / 100 burst), access logs
- **Lambda** — API (60s) and Worker (900s) functions from shared ECR image
- **SQS** — main queue + dead-letter queue (after 3 receive attempts)
- **DynamoDB** — jobs table (24h TTL) + rate-limiter table
- **S3** — results bucket (checkpoints 7d TTL, results 90d TTL) + access-logs bucket
- **CloudWatch** — log groups for API Gateway and both Lambda functions (30d retention)
- **IAM** — least-privilege roles per function

### Build and push Docker image

```bash
# Authenticate
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin 123456789.dkr.ecr.us-east-1.amazonaws.com

# Build and push
docker build -t bizgenie-ocr:latest .
docker tag bizgenie-ocr:latest 123456789.dkr.ecr.us-east-1.amazonaws.com/bizgenie-ocr:latest
docker push 123456789.dkr.ecr.us-east-1.amazonaws.com/bizgenie-ocr:latest
```

## Configuration

All settings are read from environment variables (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `MISTRAL_API_KEY` | — | Required |
| `MISTRAL_OCR_MODEL` | `mistral-ocr-latest` | OCR model |
| `MISTRAL_CHAT_MODEL` | `mistral-small-latest` | Extraction model |
| `MISTRAL_TABLE_FORMAT` | `html` | Table output format (`html` or `markdown`) |
| `MISTRAL_TIMEOUT_S` | `120` | Per-request timeout |
| `MISTRAL_MAX_RETRIES` | `4` | Retry attempts on transient errors |
| `MAX_CONCURRENT_PAGES` | `4` | Parallel extraction semaphore |
| `EXTRACT_MAX_RETRIES_PER_PAGE` | `2` | Per-page extraction retries |
| `MAX_CONTINUATIONS` | `5` | Max Lambda self-invocations per job |
| `LAMBDA_EXTRACT_CONTINUATION_ENABLED` | `false` | Enable timeout-based continuation |
| `WEBHOOK_TIMEOUT_S` | `10` | Webhook HTTP timeout |
| `WEBHOOK_MAX_RETRIES` | `3` | Webhook retry attempts |
| `MISTRAL_RATE_LIMIT_TABLE` | `""` | DynamoDB table name for rate limiting (empty = disabled) |

## Testing

```bash
# Run all tests with coverage
python -m pytest tests/ --cov=src --cov-report=term-missing -q
```

**166 tests, 96% coverage.**

Test layout:
- `tests/unit/` — pure unit tests, no AWS, all dependencies mocked
- `tests/integration/` — moto-based tests that exercise real DynamoDB/S3/SQS/Lambda logic

## Project Structure

```
src/
├── api/
│   ├── routes.py          # API Gateway event router
│   └── schemas.py         # Pydantic request/response validation
├── checkpoint/
│   └── manager.py         # S3 checkpoint save/load with idempotent DynamoDB writes
├── infra/
│   ├── rate_limiter.py    # DynamoDB-backed Mistral rate limiter
│   ├── repository.py      # DynamoDB job CRUD
│   ├── store.py           # S3 result/checkpoint storage
│   └── webhook.py         # Callback delivery with retry/backoff
├── mistral/
│   ├── client.py          # Mistral SDK wrapper (OCR + chat)
│   ├── extraction.py      # Stage 2: parallel field extraction
│   ├── ocr.py             # Stage 1: PDF → markdown per page
│   └── table_parser.py    # Markdown table → structured rows
├── models/
│   ├── job.py             # JobPayload, FieldInstruction, JobStatus
│   └── result.py          # PageResult, ExtractedField, OCRResult (frozen dataclasses)
├── pipeline/
│   ├── continuation.py    # Lambda self-invocation trigger
│   └── processor.py       # Orchestrates OCR → extraction → checkpoint → webhook
├── shared/
│   ├── config.py          # Settings loaded from env vars (lru_cached)
│   ├── exceptions.py      # Typed exceptions (JobNotFoundError, SSRFBlockedError, …)
│   ├── logging.py         # Structured JSON logger with secret redaction
│   └── url_validator.py   # Async SSRF protection (DNS resolution + private IP block)
├── workers/
│   └── sqs.py             # SQS batch handler + direct invocation handler
├── container.py           # Dependency wiring (singleton components)
└── lambda_handler.py      # Lambda entry points
scripts/
└── local_server.py        # FastAPI dev server (no AWS)
terraform/                 # All infrastructure-as-code
```

## Security Notes

- **SSRF protection** — both `pdf_url` and `callback_url` are validated: scheme must be `https` (or `http` for pdf_url), and async DNS resolution blocks private/loopback IP ranges
- **Input validation** — field keys, labels, descriptions, and metadata values are length-limited and control-character-stripped at the schema layer
- **Secret redaction** — the structured logger strips `api_key`, `token`, `password`, `authorization`, and `callback_url` from all log output
- **Idempotent writes** — DynamoDB conditional expressions prevent duplicate checkpoint writes on replayed SQS messages
- **Least-privilege IAM** — API and Worker roles have separate, scoped policies; Worker cannot call API Gateway management APIs; API cannot invoke worker-only DynamoDB operations
