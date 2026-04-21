"""Composition root — wires all dependencies once per cold start."""

import os
from functools import lru_cache

import boto3

from src.checkpoint.manager import CheckpointManager
from src.infra.rate_limiter import MistralRateLimiter
from src.infra.repository import JobRepository
from src.infra.store import ResultStore
from src.infra.webhook import WebhookClient
from src.mistral.client import MistralClient
from src.mistral.extraction import ExtractionStage
from src.mistral.ocr import OCRStage
from src.pipeline.continuation import ContinuationTrigger
from src.pipeline.processor import OCRProcessor
from src.shared.config import Settings, get_settings


class Container:
    """Holds all wired singleton dependencies."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._repo: JobRepository | None = None
        self._store: ResultStore | None = None
        self._mistral_client: MistralClient | None = None
        self._extraction_stage: ExtractionStage | None = None
        self._processor: OCRProcessor | None = None
        self._refiner = None

    def get_repo(self) -> JobRepository:
        if self._repo is None:
            self._repo = _build_repo(self.settings)
        return self._repo

    def get_store(self) -> ResultStore:
        if self._store is None:
            self._store = _build_store(self.settings)
        return self._store

    def _get_mistral_client(self) -> MistralClient:
        if self._mistral_client is None:
            self._mistral_client = _build_mistral_client(self.settings)
        return self._mistral_client

    def get_extraction_stage(self) -> ExtractionStage:
        if self._extraction_stage is None:
            self._extraction_stage = ExtractionStage(
                client=self._get_mistral_client(),
                max_concurrent_pages=self.settings.processing.max_concurrent_pages,
                max_retries_per_page=self.settings.processing.extract_max_retries_per_page,
            )
        return self._extraction_stage

    def get_processor(self) -> OCRProcessor:
        if self._processor is None:
            self._processor = _build_processor(
                self.settings,
                self.get_repo(),
                self.get_store(),
                self._get_mistral_client(),
                self.get_extraction_stage(),
            )
        return self._processor

    def get_refiner(self):
        from src.pipeline.refiner import RefineHandler
        if self._refiner is None:
            self._refiner = RefineHandler(
                store=self.get_store(),
                extraction_stage=self.get_extraction_stage(),
                repo=self.get_repo(),
            )
        return self._refiner


def _build_repo(settings: Settings) -> JobRepository:
    dynamodb = boto3.resource("dynamodb", region_name=settings.aws.region)
    table = dynamodb.Table(settings.aws.dynamodb_table)
    return JobRepository(table)


def _build_store(settings: Settings) -> ResultStore:
    minio = settings.minio
    s3_client = boto3.client(
        "s3",
        endpoint_url=minio.url,
        aws_access_key_id=minio.access_key,
        aws_secret_access_key=minio.secret_key,
        region_name="us-east-1",
    )
    return ResultStore(
        s3_client=s3_client,
        bucket=minio.bucket,
        base_url=f"{minio.url.rstrip('/')}/{minio.bucket}",
    )


def _build_rate_limiter(settings: Settings) -> MistralRateLimiter:
    rl = settings.rate_limit
    if not rl.rate_limit_table:
        return MistralRateLimiter(
            table=None,
            rps=rl.mistral_rps,
            pk=rl.rate_limit_pk,
            ttl_seconds=rl.rate_limit_ttl_seconds,
            max_wait_seconds=rl.rate_limit_max_wait_seconds,
        )
    dynamodb = boto3.resource("dynamodb", region_name=settings.aws.region)
    table = dynamodb.Table(rl.rate_limit_table)
    return MistralRateLimiter(
        table=table,
        rps=rl.mistral_rps,
        pk=rl.rate_limit_pk,
        ttl_seconds=rl.rate_limit_ttl_seconds,
        max_wait_seconds=rl.rate_limit_max_wait_seconds,
    )


def _build_mistral_client(settings: Settings) -> MistralClient:
    rate_limiter = _build_rate_limiter(settings)
    return MistralClient(
        api_key=settings.mistral.api_key,
        ocr_model=settings.mistral.ocr_model,
        chat_model=settings.mistral.chat_model,
        table_format=settings.mistral.table_format,
        base_url=settings.mistral.base_url,
        timeout_s=settings.mistral.timeout_s,
        max_retries=settings.mistral.max_retries,
        rate_limiter=rate_limiter,
    )


def _build_processor(
    settings: Settings,
    repo: JobRepository,
    store: ResultStore,
    mistral_client: MistralClient,
    extraction_stage: ExtractionStage,
) -> OCRProcessor:
    lambda_client = boto3.client("lambda", region_name=settings.aws.region)
    worker_function = os.environ.get("WORKER_FUNCTION_NAME", "")

    return OCRProcessor(
        ocr_stage=OCRStage(client=mistral_client),
        extraction_stage=extraction_stage,
        checkpoint_manager=CheckpointManager(store=store, repo=repo),
        repo=repo,
        store=store,
        webhook=WebhookClient(
            timeout_s=settings.processing.webhook_timeout_s,
            max_retries=settings.processing.webhook_max_retries,
        ),
        continuation=ContinuationTrigger(
            lambda_client=lambda_client,
            function_name=worker_function,
            max_continuations=settings.processing.max_continuations,
        ),
        settings=settings,
    )


@lru_cache(maxsize=1)
def get_container() -> Container:
    """Return the fully wired container. Built once per cold start."""
    return Container(get_settings())
