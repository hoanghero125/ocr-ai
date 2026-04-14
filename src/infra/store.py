"""ResultStore — S3 read/write for results and checkpoints with typed serialization."""

import dataclasses
import json
from typing import Any

from src.models.result import ExtractedField, ExtractedTable, OCRResult, PageResult


def _result_to_dict(result: OCRResult) -> dict:
    return dataclasses.asdict(result)


def _page_from_dict(d: dict) -> PageResult:
    return PageResult(
        page_number=d["page_number"],
        markdown=d["markdown"],
        tables=[
            ExtractedTable(
                headers=t["headers"],
                rows=t["rows"],
                raw=t["raw"],
            )
            for t in d.get("tables") or []
        ],
        fields=[
            ExtractedField(
                key=f["key"],
                label=f["label"],
                value=f["value"],
                confidence=f["confidence"],
            )
            for f in d.get("fields") or []
        ],
        error=d.get("error"),
    )


class ResultStore:
    def __init__(self, s3_client: Any, bucket: str) -> None:
        """
        Args:
            s3_client: boto3 S3 client
            bucket:    S3 bucket name
        """
        self._s3 = s3_client
        self._bucket = bucket

    def put_result(self, job_id: str, result: OCRResult) -> str:
        """Serialize and upload the final OCR result. Returns the S3 URI."""
        key = f"results/{job_id}/result.json"
        body = json.dumps(_result_to_dict(result), default=str)
        self._s3.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=body.encode(),
            ContentType="application/json",
        )
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
