"""Unit tests for Container wiring and caching."""

from unittest.mock import MagicMock, patch

import pytest

from src.container import Container, get_container
from src.infra.repository import JobRepository
from src.pipeline.processor import OCRProcessor


@pytest.fixture(autouse=True)
def reset_caches():
    from src.container import get_container
    from src.shared.config import get_settings
    get_container.cache_clear()
    get_settings.cache_clear()
    yield
    get_container.cache_clear()
    get_settings.cache_clear()


def _patched_boto3():
    return patch("src.container.boto3")


def test_get_repo_returns_job_repository():
    with _patched_boto3() as mock_boto3:
        mock_boto3.resource.return_value.Table.return_value = MagicMock()
        from src.shared.config import get_settings
        container = Container(get_settings())
        repo = container.get_repo()
    assert isinstance(repo, JobRepository)


def test_get_repo_is_cached():
    with _patched_boto3() as mock_boto3:
        mock_boto3.resource.return_value.Table.return_value = MagicMock()
        mock_boto3.client.return_value = MagicMock()
        from src.shared.config import get_settings
        container = Container(get_settings())
        assert container.get_repo() is container.get_repo()


def test_get_processor_returns_ocr_processor():
    with _patched_boto3() as mock_boto3:
        mock_boto3.resource.return_value.Table.return_value = MagicMock()
        mock_boto3.client.return_value = MagicMock()
        from src.shared.config import get_settings
        container = Container(get_settings())
        processor = container.get_processor()
    assert isinstance(processor, OCRProcessor)


def test_get_processor_is_cached():
    with _patched_boto3() as mock_boto3:
        mock_boto3.resource.return_value.Table.return_value = MagicMock()
        mock_boto3.client.return_value = MagicMock()
        from src.shared.config import get_settings
        container = Container(get_settings())
        assert container.get_processor() is container.get_processor()


def test_get_container_returns_container_instance():
    with _patched_boto3() as mock_boto3:
        mock_boto3.resource.return_value.Table.return_value = MagicMock()
        mock_boto3.client.return_value = MagicMock()
        container = get_container()
    assert isinstance(container, Container)


def test_get_container_is_cached():
    with _patched_boto3() as mock_boto3:
        mock_boto3.resource.return_value.Table.return_value = MagicMock()
        mock_boto3.client.return_value = MagicMock()
        c1 = get_container()
        c2 = get_container()
    assert c1 is c2
