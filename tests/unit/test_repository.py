"""Unit tests for JobRepository DynamoDB operations."""

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from src.infra.repository import JobRepository
from src.models.job import JobStatus
from src.models.result import JobProgress
from src.shared.exceptions import JobNotFoundError


def _make_repo() -> tuple[JobRepository, MagicMock]:
    table = MagicMock()
    return JobRepository(table), table


# ── create ────────────────────────────────────────────────────────────────────

def test_create_calls_put_item():
    repo, table = _make_repo()
    repo.create("job-1", {"job_id": "job-1"})
    table.put_item.assert_called_once()


def test_create_sets_status_queued():
    repo, table = _make_repo()
    repo.create("job-1", {})
    item = table.put_item.call_args.kwargs["Item"]
    assert item["status"] == JobStatus.QUEUED.value


def test_create_sets_ttl():
    repo, table = _make_repo()
    repo.create("job-1", {})
    item = table.put_item.call_args.kwargs["Item"]
    assert isinstance(item["ttl"], int)
    assert item["ttl"] > 0


def test_create_sets_timestamps():
    repo, table = _make_repo()
    repo.create("job-1", {})
    item = table.put_item.call_args.kwargs["Item"]
    assert "created_at" in item
    assert "updated_at" in item


# ── get ───────────────────────────────────────────────────────────────────────

def test_get_returns_item():
    repo, table = _make_repo()
    table.get_item.return_value = {"Item": {"job_id": "job-1", "status": "queued"}}
    result = repo.get("job-1")
    assert result["job_id"] == "job-1"


def test_get_raises_job_not_found_when_missing():
    repo, table = _make_repo()
    table.get_item.return_value = {}
    with pytest.raises(JobNotFoundError) as exc_info:
        repo.get("missing-job")
    assert "missing-job" in str(exc_info.value)


# ── update_status ─────────────────────────────────────────────────────────────

def test_update_status_calls_update_item():
    repo, table = _make_repo()
    repo.update_status("job-1", JobStatus.PROCESSING)
    table.update_item.assert_called_once()


def test_update_status_sets_correct_status_value():
    repo, table = _make_repo()
    repo.update_status("job-1", JobStatus.COMPLETED)
    values = table.update_item.call_args.kwargs["ExpressionAttributeValues"]
    assert values[":status"] == "completed"


def test_update_status_passes_extra_fields():
    repo, table = _make_repo()
    repo.update_status("job-1", JobStatus.COMPLETED, result_url="s3://bucket/result.json")
    values = table.update_item.call_args.kwargs["ExpressionAttributeValues"]
    assert ":extra_result_url" in values
    assert values[":extra_result_url"] == "s3://bucket/result.json"


# ── update_progress ───────────────────────────────────────────────────────────

def test_update_progress_writes_map():
    repo, table = _make_repo()
    progress = JobProgress(total_pages=5, processed_pages=3, current_step="Extracting")
    repo.update_progress("job-1", progress)
    values = table.update_item.call_args.kwargs["ExpressionAttributeValues"]
    assert values[":progress"]["total_pages"] == 5
    assert values[":progress"]["processed_pages"] == 3
    assert values[":progress"]["current_step"] == "Extracting"


# ── conditional_write_checkpoint ──────────────────────────────────────────────

def test_conditional_write_returns_true_on_success():
    repo, table = _make_repo()
    table.update_item.return_value = {}
    result = repo.conditional_write_checkpoint("job-1", "ocr-job-1", {"stage": "ocr"})
    assert result is True


def test_conditional_write_returns_false_on_duplicate():
    repo, table = _make_repo()
    table.update_item.side_effect = ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "condition failed"}},
        "UpdateItem",
    )
    result = repo.conditional_write_checkpoint("job-1", "ocr-job-1", {})
    assert result is False


def test_conditional_write_reraises_other_client_errors():
    repo, table = _make_repo()
    table.update_item.side_effect = ClientError(
        {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "throttled"}},
        "UpdateItem",
    )
    with pytest.raises(ClientError):
        repo.conditional_write_checkpoint("job-1", "ocr-job-1", {})
