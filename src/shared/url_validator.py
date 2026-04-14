"""Async SSRF protection. Validates a URL is safe before any HTTP call is made."""

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

from src.shared.exceptions import SSRFBlockedError, ValidationError

_ALLOWED_SCHEMES = {"http", "https"}

_BLOCKED_NETWORKS = [
    # RFC-1918 private
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    # Loopback
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    # Link-local + AWS IMDS (169.254.169.254)
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
    # Shared address space (RFC-6598)
    ipaddress.ip_network("100.64.0.0/10"),
    # IPv6 unique local
    ipaddress.ip_network("fc00::/7"),
    # Any / unspecified
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::/128"),
]


def _is_private(ip_str: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable → block
    return any(addr in net for net in _BLOCKED_NETWORKS)


async def validate_url(url: str) -> None:
    """
    Raise SSRFBlockedError or ValidationError if url is unsafe.
    Safe to call from async context — DNS resolution runs in an executor.
    """
    parsed = urlparse(url)

    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValidationError(f"URL scheme '{parsed.scheme}' is not allowed. Use http or https.")

    hostname = parsed.hostname
    if not hostname:
        raise ValidationError("URL must contain a hostname.")

    loop = asyncio.get_event_loop()
    try:
        results = await loop.run_in_executor(
            None,
            lambda: socket.getaddrinfo(hostname, None),
        )
    except socket.gaierror:
        raise ValidationError(f"Hostname '{hostname}' could not be resolved.")

    for result in results:
        ip = result[4][0]
        if _is_private(ip):
            # Do not reveal the resolved IP in the error message
            raise SSRFBlockedError("URL resolves to a disallowed address.")
