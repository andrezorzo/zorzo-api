"""AC1, AC2 (free quota, breaker log) and AC6: the model gateway against the fake OpenRouter server and a
real budget row."""

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from pydantic import BaseModel
from rag_core.adapters.alerts import PostgresAlertStore
from rag_core.adapters.budget import PostgresBudget
from rag_core.adapters.event_log import PostgresEventLog
from rag_core.adapters.openai_compat import OpenAICompatProvider
from rag_core.adapters.postgres import Database
from rag_core.limits import Limits, load_limits
from rag_core.models_config import load_models
from rag_core.ports import AlertKind
from rag_core.services.alerts import Alerts
from rag_core.services.breaker import Breakers
from rag_core.services.gateway import Completion, ModelGateway, ModelsUnavailable, RequestSpend
from rag_core.services.quota import Quota

from tests.fakes.openrouter import FakeOpenRouter, Reply

ROOT = Path(__file__).resolve().parents[2]
LIMITS = load_limits(ROOT / "config/limits.yaml")
CONFIG = load_models(ROOT / "tests/fixtures/models.test.yaml")
A, B, PAID = "test/answer-a:free", "test/answer-b:free", "test/answer-paid"
MESSAGES = [{"role": "user", "content": "Which AWS services has he used?"}]


class Alerter:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, kind: AlertKind, message: str) -> None:
        self.sent.append((kind, message))

    async def close(self) -> None:
        pass


@dataclass
class Rig:
    gateway: ModelGateway
    fake: FakeOpenRouter
    alerter: Alerter
    db: Database
    url: str

    def deadline(self, seconds: float = 10) -> float:
        return asyncio.get_running_loop().time() + seconds

    async def complete(self, spend: RequestSpend | None = None, **kwargs: Any) -> Completion:
        return await self.gateway.complete(
            "answer",
            MESSAGES,
            deadline_at=kwargs.pop("deadline_at", self.deadline()),
            spend=spend or RequestSpend(LIMITS.request_cost_cap_usd),
            **kwargs,
        )

    def spent_today(self) -> float:
        with psycopg.connect(self.url) as conn:
            row = conn.execute(
                "SELECT cost_usd FROM budget_daily WHERE day = (now() AT TIME ZONE 'UTC')::date"
            ).fetchone()
        return float(row[0]) if row else 0.0

    def seed_budget(self, cost: float, paid_disabled: bool = False, degraded: bool = False) -> None:
        with psycopg.connect(self.url) as conn:
            conn.execute(
                "INSERT INTO budget_daily (day, cost_usd, paid_disabled_at, degraded_at) "
                "VALUES ((now() AT TIME ZONE 'UTC')::date, %s, %s, %s) ON CONFLICT (day) DO UPDATE SET "
                "cost_usd = EXCLUDED.cost_usd, paid_disabled_at = EXCLUDED.paid_disabled_at, "
                "degraded_at = EXCLUDED.degraded_at",
                (cost, datetime.now(UTC) if paid_disabled else None, datetime.now(UTC) if degraded else None),
            )


def make_gateway(
    db: Database, fake: FakeOpenRouter, alerter: Alerter, limits: Limits = LIMITS
) -> ModelGateway:
    provider = OpenAICompatProvider("https://openrouter.test/api/v1", "test-key", transport=fake.transport())
    return ModelGateway(
        CONFIG,
        {"openrouter": provider},
        PostgresBudget(db),
        Breakers(limits, PostgresEventLog(db)),
        Quota(CONFIG, limits),
        Alerts(PostgresAlertStore(db), alerter, limits),
        limits,
    )


@pytest.fixture
async def rig(database_url: str) -> AsyncIterator[Rig]:
    with psycopg.connect(database_url) as conn:
        conn.execute("DELETE FROM budget_daily")
        conn.execute("DELETE FROM alert_states")
        conn.execute("DELETE FROM log_events")
    db = Database(database_url)
    fake, alerter = FakeOpenRouter(), Alerter()
    yield Rig(make_gateway(db, fake, alerter), fake, alerter, db, database_url)
    await db.close()


def with_limits(rig: Rig, **update: Any) -> Rig:
    rig.gateway = make_gateway(rig.db, rig.fake, rig.alerter, LIMITS.model_copy(update=update))
    return rig


async def test_answers_from_the_first_entry(rig: Rig) -> None:
    result = await rig.complete()
    assert result.text == "Hello from the fake model"
    assert (result.usage.tokens_in, result.usage.tokens_out) == (100, 20)
    assert (result.provider, result.model, result.cost_usd) == ("openrouter", A, 0.0)
    assert rig.fake.models_called() == [A]
    request = rig.fake.requests[0]
    assert request.path == "/chat/completions" and request.body["max_tokens"] == 200


@pytest.mark.parametrize("failure", ["error", "hang", "malformed", "rate_limited", "context_too_long"])
async def test_falls_to_the_next_entry_on_failure(rig: Rig, failure: str) -> None:
    with_limits(rig, timeout_answer=0.2)
    rig.fake.script(A, failure)
    result = await rig.complete()
    assert result.model == B
    assert rig.fake.models_called() == [A, B]


async def test_skips_an_open_breaker(rig: Rig) -> None:
    rig.fake.script(A, "rate_limited")
    await rig.complete()
    await rig.complete()
    assert rig.fake.models_called() == [A, B, B]


async def test_pays_when_the_free_entries_fail(rig: Rig) -> None:
    rig.fake.script(A, "error")
    rig.fake.script(B, "error")
    rig.fake.script(PAID, Reply(tokens_in=1000, tokens_out=100))
    spend = RequestSpend(LIMITS.request_cost_cap_usd)
    result = await rig.complete(spend)
    assert result.model == PAID
    assert result.cost_usd == pytest.approx(1000 * 1.0 / 1e6 + 100 * 4.0 / 1e6)
    assert spend.spent == pytest.approx(result.cost_usd)
    assert rig.spent_today() == pytest.approx(result.cost_usd, abs=1e-6)


async def test_skips_a_paid_entry_the_budget_cant_cover(rig: Rig) -> None:
    rig.fake.always(A, "error")
    rig.fake.always(B, "error")
    rig.seed_budget(LIMITS.daily_budget_usd - 0.00001)  # the day has almost nothing left
    with pytest.raises(ModelsUnavailable):
        await rig.complete()
    with pytest.raises(ModelsUnavailable):
        await rig.complete(RequestSpend(0.0))  # nor the request
    assert PAID not in rig.fake.models_called()


async def test_a_failed_paid_call_releases_its_reservation(rig: Rig) -> None:
    rig.fake.script(A, "error")
    rig.fake.script(B, "error")
    rig.fake.script(PAID, "error")
    with pytest.raises(ModelsUnavailable):
        await rig.complete()
    assert rig.spent_today() == 0.0


async def test_stops_at_the_deadline(rig: Rig) -> None:
    rig.fake.script(A, "hang")
    started = time.monotonic()
    with pytest.raises(ModelsUnavailable):
        await rig.complete(deadline_at=rig.deadline(0.3))
    assert time.monotonic() - started < 1  # the deadline, not TIMEOUT_ANSWER
    assert rig.fake.models_called() == [A]  # no time left for the next entries


async def test_raises_when_the_chain_is_exhausted(rig: Rig) -> None:
    for model in (A, B, PAID):
        rig.fake.script(model, "error")
    with pytest.raises(ModelsUnavailable):
        await rig.complete()
    assert rig.fake.models_called() == [A, B, PAID]


class Verdict(BaseModel):
    supported: bool


async def test_retries_a_schema_miss_once_on_the_same_entry(rig: Rig) -> None:
    rig.fake.script(A, Reply(text="not json"), Reply(text='{"supported": true}'))
    result = await rig.complete(schema=Verdict)
    assert result.parsed == Verdict(supported=True)
    assert rig.fake.models_called() == [A, A]
    assert rig.fake.requests[0].body["response_format"]["type"] == "json_schema"
    rig.fake.always(B, Reply(text='{"supported": false}'))
    rig.fake.script(A, Reply(text="{}"), Reply(text="{}"))
    assert (await rig.complete(schema=Verdict)).model == B  # two misses fall through
    rig.fake.script(A, Reply(text="{}"), Reply(text="{}"))
    await rig.complete(schema=Verdict)  # five misses on A by now, as many as BREAKER_FAILURES
    assert rig.gateway.breakers.closed(("openrouter", A))  # misses never count toward the breaker


async def test_stream_falls_back_only_before_the_first_token(rig: Rig) -> None:
    rig.fake.script(A, "error")
    parts = [
        p
        async for p in rig.gateway.stream(
            "answer", MESSAGES, deadline_at=rig.deadline(), spend=RequestSpend(0.01)
        )
    ]
    *tokens, final = parts
    assert "".join(t for t in tokens if isinstance(t, str)) == "Hello from the fake model"
    assert isinstance(final, Completion) and final.model == B and final.usage.tokens_out == 20

    rig.fake.script(A, "break_after_first_token")
    given: list[str] = []
    with pytest.raises(ModelsUnavailable):
        async for part in rig.gateway.stream(
            "answer", MESSAGES, deadline_at=rig.deadline(), spend=RequestSpend(0.01)
        ):
            assert isinstance(part, str)
            given.append(part)
    assert given == ["Hello"]  # the caller keeps what it was given
    assert rig.fake.models_called() == [A, B, A]  # no fallback after the first token


async def test_saves_free_quota_past_the_warning(rig: Rig) -> None:
    for _ in range(8):  # 80% of the fixture's 10 free requests a day
        await rig.gateway.complete("route", MESSAGES, deadline_at=rig.deadline(), spend=RequestSpend(0.01))
    assert len(rig.fake.requests) == 8
    with pytest.raises(ModelsUnavailable):
        await rig.gateway.rerank("q", ["a", "b"], deadline_at=rig.deadline())  # rerank goes local
    result = await rig.complete()
    assert result.model == PAID  # free entries skipped, the paid one runs while the budget allows
    assert await rig.gateway.mode() is None
    rig.seed_budget(0.09, paid_disabled=True)
    assert await rig.gateway.mode() == "quota_exhausted"
    assert rig.fake.models_called()[8:] == [PAID]


async def test_free_quota_follows_the_providers_headers(rig: Rig) -> None:
    rig.fake.script(
        A, Reply(headers={"x-ratelimit-remaining-requests": "5", "x-ratelimit-limit-requests": "100"})
    )
    await rig.complete()
    await rig.complete()
    assert rig.fake.models_called() == [A, PAID]  # 95 of 100 used: past the warning


async def test_reranks_remotely(rig: Rig) -> None:
    scores = await rig.gateway.rerank("q", ["a", "b", "c"], deadline_at=rig.deadline())
    assert scores == [3.0, 2.0, 1.0]
    assert rig.fake.requests[0].path == "/rerank"
    assert rig.fake.requests[0].body == {
        "model": "test/rerank:free",
        "query": "q",
        "documents": ["a", "b", "c"],
        "top_n": 3,
    }


async def test_breaker_transitions_are_logged(rig: Rig, database_url: str) -> None:
    rig.fake.script(A, "rate_limited")
    await rig.complete()
    await rig.gateway.drain()
    with psycopg.connect(database_url) as conn:
        rows = conn.execute("SELECT level, source, component, event, fields FROM log_events").fetchall()
    assert rows == [
        (
            "warning",
            "app",
            "breaker",
            "breaker_opened",
            {"provider": "openrouter", "model": A, "rate_limited": True},
        )
    ]


async def test_mode_follows_the_budget_and_the_breakers(rig: Rig) -> None:
    assert await rig.gateway.mode() is None
    for model in (A, B, PAID):
        rig.gateway.breakers.failure(("openrouter", model), rate_limited=True)
    assert await rig.gateway.mode() == "models_unavailable"
    rig.seed_budget(0.10, paid_disabled=True, degraded=True)
    assert await rig.gateway.mode() == "budget_exhausted"
    await rig.gateway.drain()
    assert sorted(kind for kind, _ in rig.alerter.sent) == ["budget_exhausted", "providers_down"]
