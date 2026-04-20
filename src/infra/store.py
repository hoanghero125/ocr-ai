"""ResultStore — S3 read/write for results and checkpoints with typed serialization."""

import dataclasses
import json
from typing import Any

from src.models.result import ExtractedField, ExtractedTable, FreeTextBlock, OCRResult, PageResult


def _result_to_dict(result: OCRResult) -> dict:
    payload = dataclasses.asdict(result)
    # API contract: only expose merged top-level extracted_fields.
    # Keep pages_markdown (full OCR text per page), drop pages (heavy internal objects).
    payload.pop("pages", None)
    return payload


def _page_from_dict(d: dict) -> PageResult:
    def _field(f: dict) -> ExtractedField:
        return ExtractedField(
            key=f["key"],
            label=f["label"],
            value=f["value"],
            confidence=f["confidence"],
            field_type=f.get("field_type", "typed"),
        )

    def _free_text(f: dict) -> FreeTextBlock:
        return FreeTextBlock(
            content=f["content"],
            confidence=f["confidence"],
            field_type=f.get("field_type", "typed"),
            position=f.get("position"),
        )

    return PageResult(
        page_number=d["page_number"],
        markdown=d.get("markdown", ""),
        tables=[
            ExtractedTable(
                headers=t["headers"],
                rows=t["rows"],
                raw=t["raw"],
            )
            for t in d.get("tables") or []
        ],
        extracted_fields=[_field(f) for f in d.get("extracted_fields") or []],
        free_texts=[_free_text(f) for f in d.get("free_texts") or []],
        handwritten_percentage=d.get("handwritten_percentage", 0),
        confidence=d.get("confidence", 0.0),
        status=d.get("status", "success"),
        error_message=d.get("error_message"),
        error_step=d.get("error_step"),
    )


class ResultStore:
    def __init__(self, s3_client: Any, bucket: str, base_url: str = "") -> None:
        """
        Args:
            s3_client: boto3 S3 client (or MinIO-compatible client)
            bucket:    bucket name
            base_url:  HTTP base used to build result URLs, e.g. https://minioapi.digeni.vn/mistral-ai
                       If empty, falls back to s3:// URIs.
        """
        self._s3 = s3_client
        self._bucket = bucket
        self._base_url = base_url.rstrip("/")

    def put_result(self, job_id: str, result: OCRResult) -> str:
        """Serialize and upload the final OCR result. Returns a URL to the stored object."""
        key = f"results/{job_id}/result.json"
        body = json.dumps(_result_to_dict(result), default=str)
        self._s3.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=body.encode(),
            ContentType="application/json",
        )
        if self._base_url:
            return f"{self._base_url}/{key}"
        return f"s3://{self._bucket}/{key}"

    def put_pages(self, key: str, pages: list[PageResult]) -> None:
        """Serialize and upload a checkpoint page list to S3."""
        body = json.dumps([dataclasses.asdict(p) for p in pages], default=str)
        self._s3.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=body.encode(),
            ContentType="application/json",
        )

    def get_pages(self, key: str) -> list[PageResult]:
        """Download and deserialize a checkpoint page list into fully-typed objects."""
        response = self._s3.get_object(Bucket=self._bucket, Key=key)
        raw: list[dict] = json.loads(response["Body"].read())
        return [_page_from_dict(d) for d in raw]
