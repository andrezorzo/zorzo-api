"""AC2: the day's budget row in Postgres, reserve then settle (LLD "6.2 Daily budget")."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import psycopg
import pytest
from rag_core.adapters.budget import PostgresBudget
from rag_core.adapters.postgres import Database

CAP, CUTOFF = 0.10, 0.08
NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


@pytest.fixture
async def budget(database_url: str) -> AsyncIterator[PostgresBudget]:
    with psycopg.connect(database_url) as conn:
        conn.execute("DELETE FROM budget_daily")
    db = Database(database_url)
    yield PostgresBudget(db)
    await db.close()


def row(url: str, day: date) -> tuple[object, int]:
    with psycopg.connect(url) as conn:
        found = conn.execute("SELECT cost_usd, requests FROM budget_daily WHERE day = %s", (day,)).fetchone()
    assert found is not None
    return float(found[0]), found[1]


async def test_moves_through_the_days_states(budget: PostgresBudget, database_url: str) -> None:
    day = date(2026, 11, 25)
    assert await budget.state(day) == "normal"
    assert await budget.reserve(day, 0.05, CAP)
    await budget.settle(day, 0.07 - 0.05, CUTOFF, CAP, NOW)  # it cost more than its estimate
    assert await budget.state(day) == "normal"
    assert await budget.reserve(day, 0.02, CAP)
    await budget.settle(day, 0.0, CUTOFF, CAP, NOW)  # 0.09 settled: past the cutoff
    assert await budget.state(day) == "paid_disabled"
    assert not await budget.reserve(day, 0.001, CAP)  # paid entries are off
    await budget.settle(day, 0.01, CUTOFF, CAP, NOW)  # an over-run reaches the cap
    assert await budget.state(day) == "degraded"
    assert row(database_url, day) == (pytest.approx(0.10), 2)


async def test_a_new_day_starts_normal(budget: PostgresBudget) -> None:
    day = date(2026, 11, 25)
    assert await budget.reserve(day, 0.1, CAP)
    await budget.settle(day, 0.0, CUTOFF, CAP, NOW)
    assert await budget.state(day) == "degraded"
    assert await budget.state(date(2026, 11, 26)) == "normal"
    assert await budget.reserve(date(2026, 11, 26), 0.01, CAP)


async def test_refuses_an_estimate_past_the_cap(budget: PostgresBudget) -> None:
    day = date(2026, 11, 25)
    assert not await budget.reserve(day, 0.11, CAP)  # even on a day with no row yet
    assert await budget.reserve(day, 0.06, CAP)
    assert not await budget.reserve(day, 0.05, CAP)


async def test_concurrent_reservations_never_cross_the_cap(budget: PostgresBudget, database_url: str) -> None:
    day = date(2026, 11, 25)
    results = await asyncio.gather(*(budget.reserve(day, 0.03, CAP) for _ in range(10)))
    assert sum(results) == 3
    assert row(database_url, day) == (pytest.approx(0.09), 3)


async def test_a_failed_call_releases_its_reservation(budget: PostgresBudget, database_url: str) -> None:
    day = date(2026, 11, 25)
    assert await budget.reserve(day, 0.04, CAP)
    await budget.settle(day, 0.0 - 0.04, CUTOFF, CAP, NOW)  # failed: actual 0
    assert row(database_url, day)[0] == pytest.approx(0.0)
    assert await budget.state(day) == "normal"
