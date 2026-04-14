"""Unit tests for SSRF URL validation."""

import asyncio
import socket
from unittest.mock import patch

import pytest

from src.shared.exceptions import SSRFBlockedError, ValidationError
from src.shared.url_validator import validate_url


def _mock_getaddrinfo(ip: str):
    """Return a getaddrinfo-style result resolving hostname to ip."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0))]


@pytest.mark.asyncio
async def test_private_ip_10x_blocked():
    with patch("socket.getaddrinfo", return_value=_mock_getaddrinfo("10.0.0.1")):
        with pytest.raises(SSRFBlockedError):
            await validate_url("https://internal.example.com/file.pdf")


@pytest.mark.asyncio
async def test_private_ip_172_16_blocked():
    with patch("socket.getaddrinfo", return_value=_mock_getaddrinfo("172.16.0.5")):
        with pytest.raises(SSRFBlockedError):
            await validate_url("https://internal.example.com/file.pdf")


@pytest.mark.asyncio
async def test_private_ip_192_168_blocked():
    with patch("socket.getaddrinfo", return_value=_mock_getaddrinfo("192.168.1.100")):
        with pytest.raises(SSRFBlockedError):
            await validate_url("https://internal.example.com/file.pdf")


@pytest.mark.asyncio
async def test_loopback_blocked():
    with patch("socket.getaddrinfo", return_value=_mock_getaddrinfo("127.0.0.1")):
        with pytest.raises(SSRFBlockedError):
            await validate_url("https://localhost/file.pdf")


@pytest.mark.asyncio
async def test_imds_blocked():
    with patch("socket.getaddrinfo", return_value=_mock_getaddrinfo("169.254.169.254")):
        with pytest.raises(SSRFBlockedError):
            await validate_url("https://169.254.169.254/latest/meta-data/")


@pytest.mark.asyncio
async def test_shared_address_space_blocked():
    with patch("socket.getaddrinfo", return_value=_mock_getaddrinfo("100.64.0.1")):
        with pytest.raises(SSRFBlockedError):
            await validate_url("https://carrier-grade.example.com/file.pdf")


@pytest.mark.asyncio
async def test_valid_public_url_passes():
    with patch("socket.getaddrinfo", return_value=_mock_getaddrinfo("93.184.216.34")):
        # Should not raise
        await validate_url("https://example.com/file.pdf")


@pytest.mark.asyncio
async def test_unresolvable_hostname_raises():
    with patch("socket.getaddrinfo", side_effect=socket.gaierror("Name not resolved")):
        with pytest.raises(ValidationError):
            await validate_url("https://this-does-not-exist-xyz.invalid/file.pdf")


@pytest.mark.asyncio
async def test_non_http_scheme_raises():
    with pytest.raises(ValidationError):
        await validate_url("ftp://example.com/file.pdf")


@pytest.mark.asyncio
async def test_missing_hostname_raises():
    with pytest.raises(ValidationError):
        await validate_url("https:///file.pdf")


@pytest.mark.asyncio
async def test_error_message_does_not_contain_ip():
    with patch("socket.getaddrinfo", return_value=_mock_getaddrinfo("10.0.0.1")):
        with pytest.raises(SSRFBlockedError) as exc_info:
            await validate_url("https://internal.example.com/file.pdf")
        assert "10.0.0.1" not in str(exc_info.value)
