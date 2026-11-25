"""AC4: incidents open once, remind after the repeat interval, close with one message (LLD "6.10 Alerts")."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from rag_core.adapters.alerts import PostgresAlertStore, TelegramAlerter
from rag_core.adapters.postgres import Database
from rag_core.limits import load_limits
from rag_core.services.alerts import Alerts
from rag_core.telemetry import configure_logging

from tests.fakes.alerter import RecordingAlerter

LIMITS = load_limits(Path(__file__).resolve().parents[2] / "config/limits.yaml")


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
async def db(database_url: str) -> AsyncIterator[Database]:
    with psycopg.connect(database_url) as conn:
        conn.execute("DELETE FROM alert_states")
    database = Database(database_url)
    yield database
    await database.close()


async def test_opens_once_under_concurrency(db: Database) -> None:
    alerter = RecordingAlerter()
    alerts = Alerts(PostgresAlertStore(db), alerter, LIMITS, Clock())
    await asyncio.gather(*(alerts.raise_("budget_exhausted", "spent") for _ in range(10)))
    assert alerter.sent == [("budget_exhausted", "spent")]


async def test_reminds_after_the_repeat_interval(db: Database) -> None:
    alerter, clock = RecordingAlerter(), Clock()
    alerts = Alerts(PostgresAlertStore(db), alerter, LIMITS, clock)
    await alerts.raise_("providers_down", "all open")
    clock.now += timedelta(seconds=LIMITS.alert_repeat_after - 1)
    await alerts.raise_("providers_down", "all open")
    assert len(alerter.sent) == 1
    clock.now += timedelta(seconds=2)
    await alerts.raise_("providers_down", "all open")
    await alerts.raise_("providers_down", "all open")
    assert [m for _, m in alerter.sent] == ["all open", "still open: all open"]


async def test_closes_when_the_condition_clears(db: Database, database_url: str) -> None:
    alerter = RecordingAlerter()
    alerts = Alerts(PostgresAlertStore(db), alerter, LIMITS, Clock())
    await alerts.clear("budget_exhausted")  # nothing open: no message
    await alerts.raise_("budget_exhausted", "spent")
    await alerts.clear("budget_exhausted")
    await alerts.clear("budget_exhausted")
    assert [m for _, m in alerter.sent] == ["spent", "resolved: budget_exhausted"]
    with psycopg.connect(database_url) as conn:
        closed = conn.execute("SELECT closed_at FROM alert_states WHERE kind = 'budget_exhausted'").fetchone()
    assert closed is not None and closed[0] is not None
    await alerts.raise_("budget_exhausted", "spent again")  # a new incident opens
    assert alerter.sent[-1] == ("budget_exhausted", "spent again")


async def test_a_failing_alerter_is_logged_not_raised(db: Database) -> None:
    alerts = Alerts(PostgresAlertStore(db), RecordingAlerter(fail=True), LIMITS, Clock())
    await alerts.raise_("budget_exhausted", "spent")  # no exception


async def test_a_failing_store_is_logged_not_raised() -> None:
    db = Database("postgresql://nobody:x@127.0.0.1:9/none")
    alerter = RecordingAlerter()
    await Alerts(PostgresAlertStore(db), alerter, LIMITS, Clock()).raise_("budget_exhausted", "spent")
    assert alerter.sent == []
    await db.close()


async def test_telegram_sends_and_logs_no_token(caplog: pytest.LogCaptureFixture) -> None:
    configure_logging("local", "DEBUG")
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    token = "123456:SECRET-bot-token"
    alerter = TelegramAlerter(token, "42", transport=httpx.MockTransport(handle))
    with caplog.at_level(logging.DEBUG):
        await alerter.send("budget_exhausted", "spent")
    await alerter.close()
    assert seen[0].url == f"https://api.telegram.org/bot{token}/sendMessage"
    assert json.loads(seen[0].content) == {"chat_id": "42", "text": "budget_exhausted: spent"}
    assert token not in caplog.text
    configure_logging("local", "INFO")


async def test_raise_soon_asks_the_store_at_most_once_a_minute(db: Database) -> None:
    store, opens = PostgresAlertStore(db), [0]
    real_open = store.open

    async def counted(*args: Any) -> Any:
        opens[0] += 1
        return await real_open(*args)

    store.open = counted  # type: ignore[method-assign,assignment]
    alerter = RecordingAlerter()
    alerts = Alerts(store, alerter, LIMITS, Clock(), monotonic=lambda: 0.0)
    for _ in range(5):  # one question after another during an incident
        alerts.raise_soon("budget_exhausted", "spent")
    await alerts.drain()
    assert opens == [1]
    assert alerter.sent == [("budget_exhausted", "spent")]
    alerts.clear_soon("budget_exhausted")
    alerts.raise_soon("budget_exhausted", "spent again")  # a new incident is raised at once
    await alerts.drain()
    assert [m for _, m in alerter.sent] == ["spent", "resolved: budget_exhausted", "spent again"]
