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
import dataclasses
import json
import os
import sys
import time
import uuid
from pathlib import Path

# ── Load .env before anything else imports os.environ ─────────────────────────
env_path = Path(__file__).parent.parent / ".env"
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
            print(f"\n  -- Page {page['page_number']} --")
            if page.get("fields"):
                for field in page["fields"]:
                    print(f"     {field['key']}: {field['value']}  (confidence: {field['confidence']:.2f})")
            if page.get("tables"):
                print(f"     Tables: {len(page['tables'])}")
            if page.get("error"):
                print(f"     Error: {page['error']}")


async def run(pdf_url: str, field_keys: list[str]) -> None:
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

    # Simulate API handler
    from src.api.routes import handle_api_event

    # Patch SQS send so it doesn't fail (we invoke worker directly)
    import unittest.mock as mock
    mock_sqs = mock.MagicMock()
    mock_sqs.send_message = mock.MagicMock()

    event = {
        "httpMethod": "POST",
        "path": "/process",
        "body": json.dumps({
            "pdf_url": pdf_url,
            "field_instructions": field_instructions,
        }),
    }

    container = get_container()

    with mock.patch("boto3.client", return_value=mock_sqs):
        response = await handle_api_event(event, context=None, container=container)

    if response["statusCode"] != 202:
        print(f"[local] API error: {response['body']}")
        return

    body = json.loads(response["body"])
    job_id = body["job_id"]
    print(f"[local] Job created: {job_id}")

    # Directly invoke worker (skip SQS polling)
    from src.models.job import FieldInstruction, JobPayload
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
