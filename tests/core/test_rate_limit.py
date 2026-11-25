"""AC3: token buckets in Postgres: all or nothing, and no double pass under concurrency."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from rag_core.adapters.postgres import Database
from rag_core.adapters.rate_limit import PostgresRateLimiter
from rag_core.ports import Bucket

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


@pytest.fixture
async def limiter(database_url: str) -> AsyncIterator[PostgresRateLimiter]:
    db = Database(database_url)
    yield PostgresRateLimiter(db)
    await db.close()


async def wait(limiter: PostgresRateLimiter, buckets: list[Bucket], now: datetime) -> float | None:
    """None when the take passed, else the longest wait among the empty buckets."""
    refused = await limiter.take(buckets, now)
    return None if refused is None else max(seconds for _, seconds in refused)


def burst(capacity: int = 3, window: float = 60.0) -> Bucket:
    return Bucket(f"test:{uuid.uuid4()}", "test", capacity, capacity / window)


async def test_takes_until_empty_then_says_how_long_to_wait(limiter: PostgresRateLimiter) -> None:
    bucket = burst(capacity=3, window=60)  # one token every 20 s
    assert [await wait(limiter, [bucket], NOW) for _ in range(3)] == [None, None, None]
    assert await wait(limiter, [bucket], NOW) == pytest.approx(20)
    assert await wait(limiter, [bucket], NOW + timedelta(seconds=15)) == pytest.approx(5)
    assert await wait(limiter, [bucket], NOW + timedelta(seconds=20)) is None


async def test_a_bucket_that_does_not_refill_waits_for_its_reset(limiter: PostgresRateLimiter) -> None:
    resets_at = NOW + timedelta(hours=12)
    day = Bucket(f"test:{uuid.uuid4()}", "test", 1, 0.0, resets_at)
    assert await wait(limiter, [day], NOW) is None
    assert await wait(limiter, [day], NOW + timedelta(hours=2)) == pytest.approx(10 * 3600)


async def test_a_refused_take_takes_nothing(limiter: PostgresRateLimiter) -> None:
    full, empty = burst(capacity=2), burst(capacity=1)
    assert await wait(limiter, [empty], NOW) is None
    assert await wait(limiter, [full, empty], NOW) is not None  # `empty` refuses: `full` keeps both tokens
    assert await wait(limiter, [full], NOW) is None
    assert await wait(limiter, [full], NOW) is None
    assert await wait(limiter, [full], NOW) is not None


async def test_concurrent_takes_never_pass_more_than_the_bucket_holds(limiter: PostgresRateLimiter) -> None:
    shared, other = burst(capacity=5, window=3600), burst(capacity=50, window=3600)
    results = await asyncio.gather(*(limiter.take([other, shared], NOW) for _ in range(20)))
    assert sum(r is None for r in results) == 5
