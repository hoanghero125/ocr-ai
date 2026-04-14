"""
Local end-to-end test runner.

Loads .env, spins up moto-mocked AWS resources, then runs the full
API → SQS → Worker pipeline against a real Mistral API call.

Usage:
    python scripts/local_invoke.py --pdf-url "https://example.com/sample.pdf"
    python scripts/local_invoke.py --pdf-url "https://example.com/sample.pdf" --fields name dob
"""

import argparse
import asyncio
import base64
import dataclasses
import json
import os
import sys
import time
import uuid
from pathlib import Path

# ── Ensure project root is on sys.path ────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ── Load .env before anything else imports os.environ ─────────────────────────
env_path = PROJECT_ROOT / ".env"
if env_path.exists():
    from dotenv import load_dotenv
    load_dotenv(env_path)
    print(f"[local] Loaded {env_path}")
else:
    print(f"[local] Warning: no .env file found at {env_path}")
    print(f"[local] Copy .env.example to .env and fill in MISTRAL_API_KEY")
    sys.exit(1)

# ── Force-disable continuation and rate limiting for local dev ─────────────────
os.environ.setdefault("LAMBDA_EXTRACT_CONTINUATION_ENABLED", "false")
os.environ.setdefault("MISTRAL_RATE_LIMIT_TABLE", "")
os.environ.setdefault("ENVIRONMENT", "local")

# ── Now import moto and patch AWS before any boto3 call ───────────────────────
import boto3
from moto import mock_aws


def _bootstrap_aws(table_name: str, bucket_name: str) -> None:
    """Create the DynamoDB table and S3 bucket in the moto mock."""
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    dynamodb.create_table(
        TableName=table_name,
        KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "job_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )

    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=bucket_name)
    print(f"[local] DynamoDB table '{table_name}' and S3 bucket '{bucket_name}' created (mocked)")


def _print_result(job_id: str, table_name: str) -> None:
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    table = dynamodb.Table(table_name)
    item = table.get_item(Key={"job_id": job_id}).get("Item", {})

    print("\n" + "=" * 60)
    print(f"  Job ID : {job_id}")
    print(f"  Status : {item.get('status', 'unknown')}")
    progress = item.get("progress") or {}
    if progress:
        print(f"  Pages  : {progress.get('processed_pages')}/{progress.get('total_pages')}")
        print(f"  Step   : {progress.get('current_step')}")
    if item.get("error"):
        print(f"  Error  : {item['error']}")
    if item.get("result_url"):
        print(f"  Result : {item['result_url']}")
    print("=" * 60)

    # Pull full result from S3
    result_url = item.get("result_url", "")
    if result_url.startswith("s3://"):
        parts = result_url.removeprefix("s3://").split("/", 1)
        bucket, key = parts[0], parts[1]
        s3 = boto3.client("s3", region_name="us-east-1")
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        result = json.loads(body)
        print("\nPages extracted:")
        for page in result.get("pages", []):
            print(f"\n{'─' * 60}")
            print(f"  Page {page['page_number']}")
            print(f"{'─' * 60}")
            if page.get("error"):
                print(f"  ERROR: {page['error']}")
            if page.get("markdown"):
                print(page["markdown"])
            if page.get("tables"):
                print(f"\n  [{len(page['tables'])} table(s) parsed]")
            if page.get("fields"):
                print("\n  Extracted fields:")
                for field in page["fields"]:
                    print(f"    {field['key']}: {field['value']}  (confidence: {field['confidence']:.2f})")


def _local_path_to_data_uri(path: str) -> str:
    """Convert a local file path to a base64 data URI."""
    data = Path(path).read_bytes()
    b64 = base64.b64encode(data).decode()
    return f"data:application/pdf;base64,{b64}"


async def run(pdf_url: str, field_keys: list[str]) -> None:
    # Support local file paths
    if not pdf_url.startswith(("http://", "https://", "data:")):
        print(f"[local] Reading local file: {pdf_url}")
        pdf_url = _local_path_to_data_uri(pdf_url)
        print(f"[local] Converted to data URI ({len(pdf_url)} bytes)")

    from src.shared.config import get_settings
    # Clear lru_cache so settings re-read with new env vars
    get_settings.cache_clear()

    from src.container import get_container
    get_container.cache_clear()

    table_name = os.environ["DYNAMODB_TABLE"]
    bucket_name = os.environ["S3_BUCKET"]

    _bootstrap_aws(table_name, bucket_name)

    # Build field instructions if --fields provided
    field_instructions = []
    for key in field_keys:
        field_instructions.append({
            "key": key,
            "label": key.replace("_", " ").title(),
            "description": f"Extract the {key} field",
        })

    container = get_container()

    # Build JobPayload directly (skip API route + URL validation for local files)
    import dataclasses as _dc
    from src.models.job import FieldInstruction, JobPayload
    from src.infra.repository import JobRepository

    job_id = str(uuid.uuid4())
    payload = JobPayload(
        job_id=job_id,
        pdf_url=pdf_url,
        field_instructions=tuple(
            FieldInstruction(
                key=fi["key"],
                label=fi["label"],
                description=fi.get("description", ""),
            )
            for fi in field_instructions
        ),
    )

    repo = container.get_repo()
    # Store a placeholder pdf_url in DynamoDB — data URIs exceed the 400KB item limit
    db_record = _dc.asdict(payload)
    if pdf_url.startswith("data:"):
        db_record["pdf_url"] = "data:local-file"
    repo.create(job_id, db_record)
    print(f"[local] Job created: {job_id}")

    print(f"[local] Processing job (calling Mistral OCR)...")
    t0 = time.monotonic()

    processor = container.get_processor()

    class _FakeContext:
        def get_remaining_time_in_millis(self):
            return 999_999_999

    await processor.process(payload, context=_FakeContext())

    elapsed = time.monotonic() - t0
    print(f"[local] Done in {elapsed:.1f}s")

    _print_result(job_id, table_name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local OCR pipeline runner")
    parser.add_argument("--pdf-url", required=True, help="Public HTTPS URL of a PDF to process")
    parser.add_argument(
        "--fields", nargs="*", default=[],
        help="Field keys to extract (e.g. --fields full_name date_of_birth)"
    )
    args = parser.parse_args()

    # moto mock must wrap everything including bootstrap
    with mock_aws():
        asyncio.run(run(args.pdf_url, args.fields))


if __name__ == "__main__":
    main()
