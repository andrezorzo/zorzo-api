"""The model gateway on the LiteLLM Router (F01-T13 AC1, AC2, AC3, AC5) against the fake OpenRouter server and
a real budget row."""

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import psycopg
import pytest
from pydantic import BaseModel
from rag_core.adapters.factory import build_gateway
from rag_core.adapters.litellm_models import FAILS_BEFORE_COOLDOWN
from rag_core.adapters.postgres import Database
from rag_core.limits import Limits, load_limits
from rag_core.models_config import Entry, Price, load_models
from rag_core.ports import Cooldown
from rag_core.services.gateway import (
    CHARS_PER_TOKEN,
    Completion,
    ModelGateway,
    ModelsUnavailableError,
    RequestSpend,
)

from tests.conftest import reset_tables, seed_budget, served
from tests.fakes.alerter import RecordingAlerter
from tests.fakes.openrouter import FakeOpenRouter, Reply

ROOT = Path(__file__).resolve().parents[2]
LIMITS = load_limits(ROOT / "config/limits.yaml")
CONFIG = load_models(ROOT / "tests/fixtures/models.test.yaml")
A, B, PAID = "test/answer-a:free", "test/answer-b:free", "test/answer-paid"
RERANK = "test/rerank:free"
MESSAGES = [{"role": "user", "content": "Which AWS services has he used?"}]
PAID_PRICE = cast(Price, CONFIG.stage("answer").chain[-1].price)  # a paid entry always has one
ANSWER_MAX_TOKENS = CONFIG.stage("answer").max_tokens
PROMPT_TOKENS = -(-len(MESSAGES[0]["content"]) // CHARS_PER_TOKEN)
ESTIMATE = (PROMPT_TOKENS * PAID_PRICE.input_per_mtok + ANSWER_MAX_TOKENS * PAID_PRICE.output_per_mtok) / 1e6


@dataclass
class Rig:
    gateway: ModelGateway
    fake: FakeOpenRouter
    alerter: RecordingAlerter
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

    async def stream(self, spend: float = 0.01, deadline: float = 10) -> list[str | Completion]:
        return [
            p
            async for p in self.gateway.stream(
                "answer", MESSAGES, deadline_at=self.deadline(deadline), spend=RequestSpend(spend)
            )
        ]

    def spent_today(self) -> float:
        with psycopg.connect(self.url) as conn:
            row = conn.execute(
                "SELECT cost_usd FROM budget_daily WHERE day = (now() AT TIME ZONE 'UTC')::date"
            ).fetchone()
        return float(row[0]) if row else 0.0

    def seed_budget(self, cost: float, paid_disabled: bool = False, degraded: bool = False) -> None:
        seed_budget(self.url, cost, paid_disabled, degraded)

    def log_events(self) -> list[tuple[str, str, dict[str, Any]]]:
        with psycopg.connect(self.url) as conn:
            rows = conn.execute("SELECT component, event, fields FROM log_events ORDER BY id").fetchall()
        return [(r[0], r[1], r[2]) for r in rows]


async def settle_litellm() -> None:
    """Wait for the background tasks LiteLLM spawned (its failure handlers) to finish."""
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.wait(pending, timeout=2)
    await asyncio.sleep(0.05)  # a handler it hands to a thread


def make_gateway(
    db: Database, fake: FakeOpenRouter, alerter: RecordingAlerter, limits: Limits = LIMITS
) -> ModelGateway:
    return build_gateway(served(CONFIG, fake), {"openrouter": "test-key"}, db, alerter, limits)


@pytest.fixture
async def rig(database_url: str) -> AsyncIterator[Rig]:
    reset_tables(database_url, "budget_daily", "alert_states", "log_events")
    db = Database(database_url)
    fake, alerter = FakeOpenRouter(), RecordingAlerter()
    rig = Rig(make_gateway(db, fake, alerter), fake, alerter, db, database_url)
    yield rig
    await rig.gateway.close()
    await db.close()


def with_limits(rig: Rig, **update: Any) -> Rig:
    rig.gateway = make_gateway(rig.db, rig.fake, rig.alerter, LIMITS.model_copy(update=update))
    return rig


# -- AC1: the free entries on the Router ------------------------------------------------------------------


async def test_answers_from_the_first_free_entry(rig: Rig) -> None:
    result = await rig.complete()
    assert result.text == "Hello from the fake model"
    assert (result.usage.tokens_in, result.usage.tokens_out) == (100, 20)
    assert (result.provider, result.model, result.cost_usd) == ("openrouter", A, 0.0)
    assert rig.fake.models_called() == [A]
    request = rig.fake.requests[0]
    assert request.path == "/chat/completions" and request.body["max_tokens"] == 200


@pytest.mark.parametrize(
    "failure", ["error", "hang", "malformed", "rate_limited", "context_too_long", "error_choice"]
)
async def test_a_failure_falls_to_the_next_entry(rig: Rig, failure: str) -> None:
    with_limits(rig, timeout_answer=0.3)
    rig.fake.script(A, failure)
    result = await rig.complete()
    assert result.model == B
    assert rig.fake.models_called() == [A, B]


async def test_a_429_cools_the_entry_for_its_retry_after_and_falls_through_at_once(rig: Rig) -> None:
    rig.fake.script(A, "rate_limited")  # Retry-After: 30
    started = time.monotonic()
    assert (await rig.complete()).model == B
    assert time.monotonic() - started < 2  # no retry on A, no wait for its Retry-After
    assert (await rig.complete()).model == B
    assert rig.fake.models_called() == [A, B, B]  # the cooled entry gets no request
    cooldowns = await rig.gateway.models.cooldowns()
    assert cooldowns[f"answer:openrouter/{A}"].length_s == 30


async def test_a_cooled_entry_is_tried_again_after_the_cooldown(rig: Rig) -> None:
    with_limits(rig, breaker_cooldown=0.3)  # a 401 sends no Retry-After: the Router's cooldown applies
    rig.fake.script(A, "unauthorized")
    assert (await rig.complete()).model == B
    assert (await rig.complete()).model == B
    await asyncio.sleep(0.4)
    assert (await rig.complete()).model == A
    assert rig.fake.models_called() == [A, B, B, A]


async def test_a_cooled_entry_is_skipped_and_the_next_gets_one_request(rig: Rig) -> None:
    rig.fake.script(A, "rate_limited")
    await rig.complete()  # A cools
    rig.fake.script(B, "error")
    assert (await rig.complete()).model == PAID
    assert rig.fake.models_called() == [A, B, B, PAID]  # B once per question: no retry on it


# A 500; a 200 the adapter finds failed; a 402, which LiteLLM leaves out of its count.
@pytest.mark.parametrize("failure", ["error", "error_choice", "payment_required"])
async def test_repeated_failures_cool_an_entry(rig: Rig, failure: str) -> None:
    rig.fake.always(A, failure)
    for _ in range(FAILS_BEFORE_COOLDOWN + 1):
        await rig.complete()
    assert f"answer:openrouter/{A}" in await rig.gateway.models.cooldowns()
    calls = len(rig.fake.models_called())
    assert (await rig.complete()).model == B
    assert rig.fake.models_called()[calls:] == [B]  # A cooling: no request


async def test_broken_streams_cool_an_entry_on_the_third(rig: Rig) -> None:
    rig.fake.always(A, "break_after_first_token")  # a dropped connection: LiteLLM leaves it out of its count
    for _ in range(FAILS_BEFORE_COOLDOWN):
        with pytest.raises(ModelsUnavailableError):
            await rig.stream()
    await settle_litellm()  # LiteLLM counts a stream's failure in background work: let it, if it would
    assert f"answer:openrouter/{A}" not in await rig.gateway.models.cooldowns()  # counted once each
    with pytest.raises(ModelsUnavailableError):
        await rig.stream()
    assert f"answer:openrouter/{A}" in await rig.gateway.models.cooldowns()


@pytest.mark.parametrize("stall", ["hang", "stall_after_headers"])  # before any byte; after the headers
async def test_stalling_streams_cool_an_entry_on_the_third(rig: Rig, stall: str) -> None:
    with_limits(rig, timeout_answer=0.2)
    rig.fake.always(A, stall)
    for _ in range(FAILS_BEFORE_COOLDOWN):
        await rig.stream()
    await settle_litellm()
    assert f"answer:openrouter/{A}" not in await rig.gateway.models.cooldowns()
    await rig.stream()
    await settle_litellm()
    assert f"answer:openrouter/{A}" in await rig.gateway.models.cooldowns()


async def test_a_hanging_remote_rerank_cools_on_the_third(rig: Rig) -> None:
    with_limits(rig, timeout_rerank=0.2)
    rig.fake.always(RERANK, "hang")
    for _ in range(FAILS_BEFORE_COOLDOWN + 1):
        with pytest.raises(ModelsUnavailableError):
            await rig.gateway.rerank("q", ["a", "b"])
    await settle_litellm()
    assert f"rerank:openrouter/{RERANK}" in await rig.gateway.models.cooldowns()


async def test_a_later_rerank_entry_cut_by_the_shared_window_is_not_counted(rig: Rig) -> None:
    second = Entry(provider="openrouter", model="test/rerank-b:free")
    stage = CONFIG.stage("rerank").model_copy(update={"chain": [*CONFIG.stage("rerank").chain, second]})
    config = CONFIG.model_copy(update={"stages": CONFIG.stages.model_copy(update={"rerank": stage})})
    limits = LIMITS.model_copy(update={"timeout_rerank": 0.3})
    rig.gateway = build_gateway(served(config, rig.fake), {"openrouter": "k"}, rig.db, rig.alerter, limits)
    rig.fake.always(RERANK, "error")
    rig.fake.always(second.model, "hang")  # A fails at once, then B hangs in what is left of the window
    started = time.monotonic()
    for _ in range(FAILS_BEFORE_COOLDOWN + 1):
        with pytest.raises(ModelsUnavailableError):
            await rig.gateway.rerank("q", ["a"])
    assert time.monotonic() - started < 3 * 0.3 + 1  # each question within its window
    await settle_litellm()
    assert f"rerank:openrouter/{second.model}" not in await rig.gateway.models.cooldowns()


async def test_with_the_first_rerank_entry_cooling_the_next_is_the_first_call(rig: Rig) -> None:
    second = Entry(provider="openrouter", model="test/rerank-b:free")
    stage = CONFIG.stage("rerank").model_copy(update={"chain": [*CONFIG.stage("rerank").chain, second]})
    config = CONFIG.model_copy(update={"stages": CONFIG.stages.model_copy(update={"rerank": stage})})
    limits = LIMITS.model_copy(update={"timeout_rerank": 0.3})
    rig.gateway = build_gateway(served(config, rig.fake), {"openrouter": "k"}, rig.db, rig.alerter, limits)
    rig.fake.always(RERANK, "unauthorized")  # A cools at once
    rig.fake.always(second.model, "hang")
    for _ in range(FAILS_BEFORE_COOLDOWN + 2):  # the first question: A cools and B is cut by the window
        with pytest.raises(ModelsUnavailableError):
            await rig.gateway.rerank("q", ["a"])
    await settle_litellm()
    assert f"rerank:openrouter/{second.model}" in await rig.gateway.models.cooldowns()


async def test_a_rerank_entry_refused_as_cooling_leaves_the_window_to_the_next(rig: Rig) -> None:
    second = Entry(provider="openrouter", model="test/rerank-b:free")
    stage = CONFIG.stage("rerank").model_copy(update={"chain": [*CONFIG.stage("rerank").chain, second]})
    config = CONFIG.model_copy(update={"stages": CONFIG.stages.model_copy(update={"rerank": stage})})
    limits = LIMITS.model_copy(update={"timeout_rerank": 0.3})
    rig.gateway = build_gateway(served(config, rig.fake), {"openrouter": "k"}, rig.db, rig.alerter, limits)
    rig.fake.always(RERANK, "unauthorized")
    rig.fake.always(second.model, "hang")
    with pytest.raises(ModelsUnavailableError):
        await rig.gateway.rerank("q", ["a"])  # A cools; B, a later call here, is cut uncounted
    real_cooldowns = rig.gateway.models.cooldowns

    async def stale() -> dict[str, Cooldown]:  # this request hasn't seen A cool: A is called and refused
        return {}

    rig.gateway.models.cooldowns = stale  # type: ignore[method-assign]
    for _ in range(FAILS_BEFORE_COOLDOWN + 1):
        with pytest.raises(ModelsUnavailableError):
            await rig.gateway.rerank("q", ["a"])
    await settle_litellm()
    assert f"rerank:openrouter/{second.model}" in await real_cooldowns()


async def test_running_out_of_the_requests_time_never_cools_an_entry(rig: Rig) -> None:
    rig.fake.always(A, "hang")
    for _ in range(FAILS_BEFORE_COOLDOWN + 2):
        with pytest.raises(ModelsUnavailableError):
            await rig.complete(deadline_at=rig.deadline(0.2))  # the deadline, not TIMEOUT_ANSWER, cuts it
    await settle_litellm()
    assert f"answer:openrouter/{A}" not in await rig.gateway.models.cooldowns()


async def test_a_prompt_too_long_never_cools_an_entry(rig: Rig) -> None:
    rig.fake.always(A, "context_too_long")  # the visitor's question, not the entry: it must not cool it
    for _ in range(FAILS_BEFORE_COOLDOWN + 2):
        await rig.complete()
    assert f"answer:openrouter/{A}" not in await rig.gateway.models.cooldowns()


class Verdict(BaseModel):
    supported: bool


async def test_a_model_miss_is_retried_once_on_the_entry_then_the_chain_moves_on(rig: Rig) -> None:
    rig.fake.script(A, Reply(text="not json"), Reply(text='{"supported": true}'))
    result = await rig.complete(schema=Verdict)
    assert result.parsed == Verdict(supported=True)
    assert rig.fake.models_called() == [A, A]
    assert rig.fake.requests[0].body["response_format"]["type"] == "json_schema"
    rig.fake.script(A, Reply(text=""), Reply(text="{}"))  # an empty answer, then a schema miss
    rig.fake.script(B, Reply(text='{"supported": false}'))
    assert (await rig.complete(schema=Verdict)).model == B
    assert not await rig.gateway.models.cooldowns()  # misses are the model's: no entry cools


# -- AC2: the paid entry ----------------------------------------------------------------------------------


async def test_pays_when_the_free_entries_fail(rig: Rig) -> None:
    rig.fake.script(A, "error")
    rig.fake.script(B, "error")
    rig.fake.script(PAID, Reply(tokens_in=1000, tokens_out=100))
    spend = RequestSpend(LIMITS.request_cost_cap_usd)
    result = await rig.complete(spend)
    assert result.model == PAID
    price = (1000 * PAID_PRICE.input_per_mtok + 100 * PAID_PRICE.output_per_mtok) / 1e6
    assert result.cost_usd == pytest.approx(price)  # usage at the configured price
    assert spend.spent == pytest.approx(result.cost_usd)
    assert rig.spent_today() == pytest.approx(result.cost_usd, abs=1e-6)
    assert rig.fake.models_called() == [A, B, PAID]


async def test_settles_to_the_reported_cost(rig: Rig) -> None:
    rig.fake.script(A, "error")
    rig.fake.script(B, "error")
    rig.fake.script(PAID, Reply(tokens_in=1000, tokens_out=100, cost=0.000321))
    result = await rig.complete()
    assert result.cost_usd == pytest.approx(0.000321)
    assert rig.spent_today() == pytest.approx(0.000321, abs=1e-6)


async def test_a_negative_reported_cost_is_ignored(rig: Rig) -> None:
    rig.fake.script(A, "error")
    rig.fake.script(B, "error")
    rig.fake.script(PAID, Reply(tokens_in=1000, tokens_out=100, cost=-0.5))
    result = await rig.complete()
    price = (1000 * PAID_PRICE.input_per_mtok + 100 * PAID_PRICE.output_per_mtok) / 1e6
    assert result.cost_usd == pytest.approx(price)  # never less than spent, never a broken number


async def test_a_reported_cost_far_past_the_estimate_settles_at_twice_the_estimate(rig: Rig) -> None:
    rig.fake.script(A, "error")
    rig.fake.script(B, "error")
    rig.fake.script(PAID, Reply(cost=250.0))  # a provider's bug: one answer can't cost the day's budget
    result = await rig.complete()
    assert result.cost_usd == pytest.approx(2 * ESTIMATE)
    assert rig.spent_today() == pytest.approx(2 * ESTIMATE, abs=1e-6)


async def test_skips_a_paid_entry_the_budget_cant_cover(rig: Rig) -> None:
    rig.fake.always(A, "error")
    rig.fake.always(B, "error")
    rig.seed_budget(LIMITS.daily_budget_usd - 0.00001)  # the day has almost nothing left
    with pytest.raises(ModelsUnavailableError):
        await rig.complete()
    assert PAID not in rig.fake.models_called()


@pytest.mark.parametrize(
    ("failure", "refunded"),
    [("unavailable", True), ("bad_request", True), ("bad_gateway", False), ("error", False)],
)
async def test_only_a_refused_paid_call_releases_its_reservation(
    rig: Rig, failure: str, refunded: bool
) -> None:
    rig.fake.script(A, "error")
    rig.fake.script(B, "error")
    rig.fake.script(PAID, failure)
    with pytest.raises(ModelsUnavailableError):
        await rig.complete()
    assert (rig.spent_today() == 0.0) is refunded  # a 502 or 500 may come after the model ran


async def test_a_paid_call_that_may_have_run_keeps_its_estimate(rig: Rig) -> None:
    with_limits(rig, timeout_answer=0.3)
    rig.fake.script(A, "error")
    rig.fake.script(B, "error")
    rig.fake.script(PAID, "hang")  # the provider may still bill a call it never answered
    with pytest.raises(ModelsUnavailableError):
        await rig.complete()
    assert rig.spent_today() == pytest.approx(ESTIMATE, abs=1e-6)


async def test_a_paid_answer_without_usage_keeps_its_estimate(rig: Rig) -> None:
    rig.fake.script(A, "error")
    rig.fake.script(B, "error")
    rig.fake.script(PAID, Reply(tokens_in=0, tokens_out=0))
    result = await rig.complete()
    assert result.cost_usd == pytest.approx(ESTIMATE)
    assert rig.spent_today() == pytest.approx(ESTIMATE, abs=1e-6)


async def test_a_reservation_its_caller_left_is_given_back(rig: Rig) -> None:
    budget = rig.gateway.budget
    real_reserve = budget.reserve

    async def slow_reserve(*args: Any) -> bool:
        await asyncio.sleep(0.1)  # the commit lands after the caller has left
        return await real_reserve(*args)

    budget.reserve = slow_reserve  # type: ignore[method-assign,assignment]
    rig.fake.script(A, "error")
    rig.fake.script(B, "error")
    task = asyncio.create_task(rig.complete())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await rig.gateway.drain()
    assert rig.spent_today() == 0.0


# -- AC3: streaming, the deadline and the request cap ------------------------------------------------------


async def test_stream_falls_back_only_before_the_first_token(rig: Rig) -> None:
    rig.fake.script(A, "error")
    *tokens, final = await rig.stream()
    assert "".join(t for t in tokens if isinstance(t, str)) == "Hello from the fake model"
    assert isinstance(final, Completion) and final.model == B and final.usage.tokens_out == 20

    rig.fake.script(A, "break_after_first_token")
    given: list[str] = []
    with pytest.raises(ModelsUnavailableError):
        async for part in rig.gateway.stream(
            "answer", MESSAGES, deadline_at=rig.deadline(), spend=RequestSpend(0.01)
        ):
            assert isinstance(part, str)
            given.append(part)
    assert given == ["Hello"]  # the caller keeps what it was given
    assert rig.fake.models_called() == [A, B, A]  # no fallback after the first token


async def test_a_stream_without_real_text_is_retried_then_the_chain_moves_on(rig: Rig) -> None:
    rig.fake.script(A, "no_text", "no_text")
    *tokens, final = await rig.stream()
    assert tokens[0] == "Hello"  # nothing of the empty streams reached the visitor
    assert isinstance(final, Completion) and final.model == B


async def test_a_paid_stream_that_breaks_after_text_keeps_its_estimate(rig: Rig) -> None:
    rig.fake.script(A, "error")
    rig.fake.script(B, "error")
    rig.fake.script(PAID, "break_after_first_token")  # a dropped connection: not a refusal
    with pytest.raises(ModelsUnavailableError):
        await rig.stream()
    assert rig.spent_today() == pytest.approx(ESTIMATE, abs=1e-6)  # the provider ran: no refund


async def test_a_paid_stream_settles_at_its_estimate_without_a_reported_cost(rig: Rig) -> None:
    rig.fake.script(A, "error")
    rig.fake.script(B, "error")
    *_, final = await rig.stream()
    assert isinstance(final, Completion) and final.model == PAID
    # A stream's counts may be LiteLLM's own tokenizer's, not the provider's: never billed from them.
    assert final.cost_usd == pytest.approx(ESTIMATE)
    assert rig.spent_today() == pytest.approx(ESTIMATE, abs=1e-6)


async def test_stops_at_the_deadline(rig: Rig) -> None:
    rig.fake.script(A, "hang")
    started = time.monotonic()
    with pytest.raises(ModelsUnavailableError):
        await rig.complete(deadline_at=rig.deadline(0.3))
    assert time.monotonic() - started < 1  # the deadline, not TIMEOUT_ANSWER
    assert rig.fake.models_called() == [A]  # no time left for the next entries


async def test_the_request_cap_stops_the_paid_entry(rig: Rig) -> None:
    rig.fake.script(A, "error")
    rig.fake.script(B, "error")
    with pytest.raises(ModelsUnavailableError):
        await rig.complete(RequestSpend(0.0))
    assert PAID not in rig.fake.models_called()
    assert rig.spent_today() == 0.0


# -- the remote rerank (AC4 through the route, in tests/api) ------------------------------------------------


async def test_reranks_remotely(rig: Rig) -> None:
    scores = await rig.gateway.rerank("q", ["a", "b", "c"], deadline_at=rig.deadline())
    assert scores == [3.0, 2.0, 1.0]
    request = rig.fake.requests[0]
    assert request.path == "/rerank"
    body = request.body
    assert (body["model"], body["query"], body["documents"], body["top_n"]) == (
        RERANK,
        "q",
        ["a", "b", "c"],
        3,
    )


async def test_a_hanging_remote_rerank_ends_within_its_timeout(rig: Rig) -> None:
    with_limits(rig, timeout_rerank=0.2)
    rig.fake.script(RERANK, "hang")
    started = time.monotonic()
    with pytest.raises(ModelsUnavailableError):
        await rig.gateway.rerank("q", ["a", "b"])
    assert time.monotonic() - started < 1  # the local level still has time


# -- AC5: mode, alerts and the cooldown log ------------------------------------------------------------------


async def test_mode_follows_the_budget_and_the_cooldowns(rig: Rig) -> None:
    assert await rig.gateway.mode() is None
    rig.fake.script(A, "unauthorized")
    rig.fake.script(B, "unauthorized")
    assert (await rig.complete()).model == PAID
    assert await rig.gateway.mode() is None  # every free entry cooling, but the paid one can still run
    rig.seed_budget(0.08, paid_disabled=True)
    assert await rig.gateway.mode() == "models_unavailable"
    rig.seed_budget(0.10, paid_disabled=True, degraded=True)
    assert await rig.gateway.mode() == "budget_exhausted"
    await rig.gateway.drain()
    assert sorted(kind for kind, _ in rig.alerter.sent) == ["budget_exhausted", "providers_down"]


async def test_quota_exhausted_when_every_free_answer_entry_was_cooled_by_a_429(rig: Rig) -> None:
    rig.seed_budget(0.08, paid_disabled=True)
    rig.fake.script(A, "rate_limited")
    rig.fake.script(B, "rate_limited")
    with pytest.raises(ModelsUnavailableError):
        await rig.complete()
    assert await rig.gateway.mode() == "quota_exhausted"


async def test_cooldown_starts_and_ends_are_logged(rig: Rig) -> None:
    with_limits(rig, breaker_cooldown=0.3)
    rig.fake.script(A, "unauthorized")
    await rig.complete()
    await rig.gateway.drain()  # the start's row first: rows are written in the background
    await asyncio.sleep(0.4)
    await rig.gateway.mode()
    await rig.gateway.drain()
    entry = f"answer:openrouter/{A}"
    assert rig.log_events() == [
        ("gateway", "cooldown_started", {"entry": entry, "status": "401", "seconds": 0.3}),
        ("gateway", "cooldown_ended", {"entry": entry}),
    ]


async def test_a_cooldown_that_restarts_between_looks_is_logged_as_both(rig: Rig) -> None:
    entry = f"answer:openrouter/{A}"
    looks = iter([{entry: Cooldown("429", 1.0, 30.0)}, {entry: Cooldown("401", 40.0, 60.0)}])

    async def cooldowns() -> dict[str, Cooldown]:
        return next(looks)

    rig.gateway.models.cooldowns = cooldowns  # type: ignore[method-assign]
    await rig.gateway._note_cooldowns()
    await rig.gateway._note_cooldowns()
    await rig.gateway.drain()
    # The rows are written in the background, so in no fixed order.
    events = sorted((event, fields.get("status")) for _, event, fields in rig.log_events())
    assert events == [("cooldown_ended", None), ("cooldown_started", "401"), ("cooldown_started", "429")]


async def test_a_cooldown_renewed_before_it_ended_is_the_same_cooldown(rig: Rig) -> None:
    entry = f"answer:openrouter/{A}"
    looks = iter([{entry: Cooldown("429", 1.0, 30.0)}, {entry: Cooldown("429", 10.0, 30.0)}])

    async def cooldowns() -> dict[str, Cooldown]:
        return next(looks)

    rig.gateway.models.cooldowns = cooldowns  # type: ignore[method-assign]
    await rig.gateway._note_cooldowns()
    await rig.gateway._note_cooldowns()  # renewed at 10 s, while the first ran until 31 s
    await rig.gateway.drain()
    assert [event for _, event, _ in rig.log_events()] == ["cooldown_started"]


async def test_a_cooldown_renewed_for_another_cause_is_logged_as_a_restart(rig: Rig) -> None:
    entry = f"answer:openrouter/{A}"
    looks = iter([{entry: Cooldown("429", 1.0, 30.0)}, {entry: Cooldown("401", 10.0, 60.0)}])

    async def cooldowns() -> dict[str, Cooldown]:
        return next(looks)

    rig.gateway.models.cooldowns = cooldowns  # type: ignore[method-assign]
    await rig.gateway._note_cooldowns()
    await rig.gateway._note_cooldowns()  # renewed inside the first's window, but for an auth failure
    await rig.gateway.drain()
    events = sorted((event, fields.get("status")) for _, event, fields in rig.log_events())
    assert events == [("cooldown_ended", None), ("cooldown_started", "401"), ("cooldown_started", "429")]


async def test_the_budget_alert_opens_at_the_paid_cutoff(rig: Rig) -> None:
    rig.seed_budget(0.08, paid_disabled=True)
    assert await rig.gateway.mode() is None  # the free entries still answer
    await rig.gateway.drain()
    assert [kind for kind, _ in rig.alerter.sent] == ["budget_exhausted"]


async def test_the_alert_says_when_the_cutoff_becomes_search_only(rig: Rig) -> None:
    ticks = [0.0]
    rig.gateway.alerts.monotonic = lambda: ticks[0]
    rig.seed_budget(0.08, paid_disabled=True)
    await rig.gateway.mode()
    await rig.gateway.drain()
    rig.seed_budget(0.10, paid_disabled=True, degraded=True)
    for _ in range(2):  # the next check, a minute later; then another, with nothing new to tell
        ticks[0] += 61
        await rig.gateway.mode()
        await rig.gateway.drain()
    messages = [m for _, m in rig.alerter.sent]
    assert len(messages) == 2
    assert "cutoff" in messages[0]
    assert messages[1].startswith("still open: ") and "search-only" in messages[1]  # told within a minute
