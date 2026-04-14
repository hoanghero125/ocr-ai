"""
FastAPI dev server for local OCR testing.

Reuses the same Container + OCRProcessor as production.
AWS (DynamoDB, S3) is mocked via moto for the lifetime of the server.

Usage:
    pip install -r requirements-dev.txt
    python scripts/serve.py
    # then open http://localhost:8000/docs
"""

import base64
import dataclasses
import json
import os
import sys
import uuid
from pathlib import Path

# ── Project root on sys.path ──────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ── Load .env ─────────────────────────────────────────────────────────────────
env_path = PROJECT_ROOT / ".env"
if env_path.exists():
    from dotenv import load_dotenv
    load_dotenv(env_path)
else:
    print("[serve] Warning: no .env found — copy .env.example to .env")
    sys.exit(1)

# ── Force local-safe settings ─────────────────────────────────────────────────
os.environ.setdefault("LAMBDA_EXTRACT_CONTINUATION_ENABLED", "false")
os.environ.setdefault("MISTRAL_RATE_LIMIT_TABLE", "")
os.environ.setdefault("ENVIRONMENT", "local")

# ── Start moto mock before any boto3 import ───────────────────────────────────
import boto3
from moto import mock_aws

_mock = mock_aws()
_mock.start()


def _bootstrap_aws() -> None:
    table_name = os.environ["DYNAMODB_TABLE"]
    bucket_name = os.environ["S3_BUCKET"]

    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    dynamodb.create_table(
        TableName=table_name,
        KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "job_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=bucket_name)
    print(f"[serve] DynamoDB '{table_name}' + S3 '{bucket_name}' ready (mocked)")


_bootstrap_aws()

# ── Clear lru_cache so settings re-read with new env vars ─────────────────────
from src.shared.config import get_settings
from src.container import get_container

get_settings.cache_clear()
get_container.cache_clear()

# ── FastAPI app ───────────────────────────────────────────────────────────────
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(
    title="OCR AI — Local Dev",
    version="6.0.0",
    description="Same pipeline as production. AWS mocked via moto.",
)


# ── Request / response schemas ────────────────────────────────────────────────

class FieldIn(BaseModel):
    key: str
    label: str
    description: str = ""
    min_confidence: float = 0.0


class ProcessIn(BaseModel):
    pdf_url: str
    field_instructions: list[FieldIn] = []
    callback_url: str | None = None
    metadata: dict = {}


# ── Fake Lambda context ───────────────────────────────────────────────────────

class _FakeContext:
    def get_remaining_time_in_millis(self) -> int:
        return 999_999_999


# ── Background job runner ─────────────────────────────────────────────────────

async def _run_job(job_id: str, pdf_url: str, field_instructions: list[FieldIn]) -> None:
    from src.models.job import FieldInstruction, JobPayload

    # Convert local file path → base64 data URI
    if not pdf_url.startswith(("http://", "https://", "data:")):
        data = Path(pdf_url).read_bytes()
        pdf_url = f"data:application/pdf;base64,{base64.b64encode(data).decode()}"

    payload = JobPayload(
        job_id=job_id,
        pdf_url=pdf_url,
        field_instructions=tuple(
            FieldInstruction(
                key=fi.key,
                label=fi.label,
                description=fi.description,
                min_confidence=fi.min_confidence,
            )
            for fi in field_instructions
        ),
    )

    container = get_container()
    processor = container.get_processor()
    await processor.process(payload, context=_FakeContext())


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/process", status_code=202)
async def process(body: ProcessIn, background_tasks: BackgroundTasks):
    """Submit a PDF for OCR + extraction. Accepts local file paths or https:// URLs."""
    from src.models.job import FieldInstruction, JobPayload

    job_id = str(uuid.uuid4())

    # Store a safe placeholder — data URIs are too large for DynamoDB
    stored_url = "data:local-file" if not body.pdf_url.startswith(("http://", "https://")) else body.pdf_url

    payload_record = dataclasses.asdict(JobPayload(
        job_id=job_id,
        pdf_url=stored_url,
        field_instructions=tuple(
            FieldInstruction(key=fi.key, label=fi.label, description=fi.description, min_confidence=fi.min_confidence)
            for fi in body.field_instructions
        ),
    ))

    container = get_container()
    container.get_repo().create(job_id, payload_record)

    background_tasks.add_task(_run_job, job_id, body.pdf_url, body.field_instructions)

    return {"job_id": job_id, "status": "queued", "status_url": f"/jobs/{job_id}"}


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    """Check job status and progress."""
    from src.shared.exceptions import JobNotFoundError

    try:
        return get_container().get_repo().get(job_id)
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")


@app.get("/jobs/{job_id}/result")
async def get_result(job_id: str):
    """Fetch the full extracted result from S3 (only available when status=completed)."""
    from src.shared.exceptions import JobNotFoundError

    try:
        item = get_container().get_repo().get(job_id)
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    result_url = item.get("result_url", "")
    if not result_url.startswith("s3://"):
        raise HTTPException(status_code=404, detail="Result not ready yet")

    parts = result_url.removeprefix("s3://").split("/", 1)
    bucket, key = parts[0], parts[1]
    s3 = boto3.client("s3", region_name="us-east-1")
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    return json.loads(body)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
