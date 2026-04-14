"""Unit tests for MistralRateLimiter."""

from unittest.mock import MagicMock, patch

import pytest

from src.infra.rate_limiter import MistralRateLimiter
from src.shared.exceptions import RateLimitTimeoutError


def _make_limiter(table: MagicMock | None, rps: int = 5, max_wait: int = 2) -> MistralRateLimiter:
    return MistralRateLimiter(
        table=table,
        rps=rps,
        pk="mistral",
        ttl_seconds=120,
        max_wait_seconds=max_wait,
    )


def _mock_table_returning(count: int) -> MagicMock:
    table = MagicMock()
    table.update_item.return_value = {"Attributes": {"count": count}}
    return table


@pytest.mark.asyncio
async def test_acquire_returns_when_slot_available():
    table = _mock_table_returning(1)  # count=1, rps=5 → slot available
    limiter = _make_limiter(table, rps=5)
    await limiter.acquire()  # should not raise


@pytest.mark.asyncio
async def test_acquire_blocks_when_over_rps_then_succeeds():
    table = MagicMock()
    # First call: over limit (count=6 > rps=5)
    # Second call: under limit (count=1)
    table.update_item.side_effect = [
        {"Attributes": {"count": 6}},
        {"Attributes": {"count": 1}},
    ]
    limiter = _make_limiter(table, rps=5, max_wait=10)

    with patch("asyncio.sleep"):
        await limiter.acquire()

    assert table.update_item.call_count == 2


@pytest.mark.asyncio
async def test_disabled_when_table_is_none():
    limiter = _make_limiter(table=None)
    assert limiter.disabled is True
    await limiter.acquire()  # should be a no-op, no error


@pytest.mark.asyncio
async def test_raises_rate_limit_timeout_after_max_wait():
    table = _mock_table_returning(99)  # always over limit
    limiter = _make_limiter(table, rps=5, max_wait=0)  # immediate timeout

    with patch("asyncio.sleep"):
        with pytest.raises(RateLimitTimeoutError):
            await limiter.acquire()


@pytest.mark.asyncio
async def test_ttl_set_on_counter_item():
    table = _mock_table_returning(1)
    limiter = _make_limiter(table, rps=5)
    await limiter.acquire()

    call_kwargs = table.update_item.call_args.kwargs
    assert ":ttl_val" in call_kwargs["ExpressionAttributeValues"]
