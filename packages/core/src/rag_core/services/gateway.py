"""Model gateway (ADR 0007, LLD "Components and dependencies"): every model call goes through a stage's chain.

Each chain entry is tried in order, within `min(TIMEOUT_<STAGE>, time left)`; an entry is skipped when its
breaker is open (LLD "6.6 Circuit breaker"), when it is free and its provider passed the quota warning (LLD
"6.8 Free quota"), or when it is paid and its estimate doesn't fit in what the day or the request has left
(LLD "6.2 Daily budget"). A failure falls to the next entry; an exhausted chain raises `ModelsUnavailable`.
"""

import asyncio
import math
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TypeVar

import structlog
from pydantic import BaseModel, ValidationError

from rag_core.events import DegradedReason
from rag_core.limits import Limits
from rag_core.models_config import Entry, ModelsConfig, StageName
from rag_core.ports import (
    Budget,
    ContextTooLong,
    ModelProvider,
    ProviderError,
    RateInfo,
    RateLimited,
    StoreUnavailable,
)
from rag_core.services.alerts import Alerts
from rag_core.services.breaker import Breakers
from rag_core.services.quota import Quota

log = structlog.get_logger()

T = TypeVar("T")
CHARS_PER_TOKEN = 3  # conservative for English and Portuguese, no tokenizer (LLD "6.2 Daily budget")


class ModelsUnavailable(Exception):
    """The stage's chain is exhausted: every entry failed or was skipped."""


@dataclass(frozen=True)
class Usage:
    tokens_in: int
    tokens_out: int


@dataclass(frozen=True)
class Completion:
    text: str
    parsed: BaseModel | None
    usage: Usage
    cost_usd: float
    provider: str
    model: str


@dataclass
class RequestSpend:
    """One request's paid spend against `REQUEST_COST_CAP_USD`; reservations count until they settle."""

    cap: float
    spent: float = 0.0

    def fits(self, estimate: float) -> bool:
        return self.spent + estimate <= self.cap


class _SchemaMiss(Exception):
    """The output parsed as text but not as the requested schema: the model missed, the provider worked."""


@dataclass
class _Ticket:
    """One call let through: its entry, and the reservation to settle (0 for a free entry)."""

    stage: StageName
    entry: Entry
    estimate: float
    day: date | None
    spend: RequestSpend


@dataclass(frozen=True)
class _Outcome:
    tokens_in: int
    tokens_out: int
    rate: RateInfo | None


class ModelGateway:
    def __init__(
        self,
        config: ModelsConfig,
        providers: Mapping[str, ModelProvider],
        budget: Budget,
        breakers: Breakers,
        quota: Quota,
        alerts: Alerts,
        limits: Limits,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.config = config
        self.providers = providers
        self.budget = budget
        self.breakers = breakers
        self.quota = quota
        self.alerts = alerts
        self.limits = limits
        self.clock = clock
        self._tasks: set[asyncio.Task[None]] = set()

    # -- operations ---------------------------------------------------------------------------------------

    async def complete(
        self,
        stage: StageName,
        messages: list[dict[str, str]],
        *,
        schema: type[BaseModel] | None = None,
        deadline_at: float,
        spend: RequestSpend,
    ) -> Completion:
        json_schema = schema.model_json_schema() if schema is not None else None
        chars = sum(len(m.get("content", "")) for m in messages)
        for entry in self.config.stage(stage).chain:
            # A schema miss is retried once on the same entry, then falls through (LLD "6.6 Circuit breaker").
            for _ in range(2 if schema is not None else 1):
                ticket = await self._take(stage, entry, chars, deadline_at, spend)
                if ticket is None:
                    break
                provider = self.providers[entry.provider]
                max_tokens = self.config.stage(stage).max_tokens
                try:
                    async with asyncio.timeout(self._time_for(stage, deadline_at)):
                        result = await provider.chat(
                            entry.model, messages, max_tokens=max_tokens, schema=json_schema
                        )
                    parsed = schema.model_validate_json(result.text) if schema is not None else None
                except ValidationError:
                    await self._close(
                        ticket, _SchemaMiss(), _Outcome(result.tokens_in, result.tokens_out, result.rate)
                    )
                    continue
                except BaseException as exc:
                    await self._close(ticket, exc)
                    if isinstance(exc, ProviderError | TimeoutError):
                        break
                    raise
                cost = await self._close(
                    ticket, None, _Outcome(result.tokens_in, result.tokens_out, result.rate)
                )
                return Completion(
                    result.text,
                    parsed,
                    Usage(result.tokens_in, result.tokens_out),
                    cost,
                    entry.provider,
                    entry.model,
                )
            if self._out_of_time(deadline_at):
                break
        raise ModelsUnavailable(stage)

    async def stream(
        self, stage: StageName, messages: list[dict[str, str]], *, deadline_at: float, spend: RequestSpend
    ) -> AsyncIterator[str | Completion]:
        """Yields the answer's text as it comes, then its `Completion`.

        A failure before the first token falls to the next entry; after it, `ModelsUnavailable`, and the
        caller keeps the text it was given.
        """
        chars = sum(len(m.get("content", "")) for m in messages)
        max_tokens = self.config.stage(stage).max_tokens
        for entry in self.config.stage(stage).chain:
            ticket = await self._take(stage, entry, chars, deadline_at, spend)
            if ticket is None:
                if self._out_of_time(deadline_at):
                    break
                continue
            parts: list[str] = []
            tokens_in = tokens_out = 0
            try:
                async with asyncio.timeout(self._time_for(stage, deadline_at)):
                    async for chunk in self.providers[entry.provider].chat_stream(
                        entry.model, messages, max_tokens=max_tokens
                    ):
                        if chunk.tokens_in is not None:
                            tokens_in, tokens_out = chunk.tokens_in, chunk.tokens_out or 0
                        if chunk.text:
                            parts.append(chunk.text)
                            yield chunk.text
            except BaseException as exc:
                await self._close(ticket, exc)
                if isinstance(exc, ProviderError | TimeoutError):
                    if parts:
                        raise ModelsUnavailable(f"{stage} failed after the first token") from exc
                    continue
                raise
            cost = await self._close(ticket, None, _Outcome(tokens_in, tokens_out, None))
            yield Completion(
                "".join(parts), None, Usage(tokens_in, tokens_out), cost, entry.provider, entry.model
            )
            return
        raise ModelsUnavailable(stage)

    async def rerank(self, query: str, documents: list[str], *, deadline_at: float) -> list[float]:
        """Scores per document from the `rerank` stage; `ModelsUnavailable` sends the caller to local."""
        chars = len(query) + sum(len(d) for d in documents)
        spend = RequestSpend(self.limits.request_cost_cap_usd)
        for entry in self.config.stage("rerank").chain:
            ticket = await self._take("rerank", entry, chars, deadline_at, spend)
            if ticket is None:
                continue
            try:
                async with asyncio.timeout(self._time_for("rerank", deadline_at)):
                    result = await self.providers[entry.provider].rerank(entry.model, query, documents)
            except BaseException as exc:
                await self._close(ticket, exc)
                if isinstance(exc, ProviderError | TimeoutError):
                    continue
                raise
            await self._close(ticket, None, _Outcome(chars // CHARS_PER_TOKEN, 0, result.rate))
            return result.scores
        raise ModelsUnavailable("rerank")

    async def mode(self) -> DegradedReason | None:
        """None when questions can be answered by the models now; otherwise why they go search-only.

        Raises `StoreUnavailable` when the budget row can't be read.
        """
        state = await self.budget.state(self.clock().date())
        if state == "degraded":
            message = (
                f"today's budget of US${self.limits.daily_budget_usd} is spent; questions go search-only"
            )
            self.alerts.raise_soon("budget_exhausted", message)
            return "budget_exhausted"
        self.alerts.clear_soon("budget_exhausted")  # a new UTC day
        answer = self.config.stage("answer").chain
        keys = [(e.provider, e.model) for e in answer]
        if any(self.breakers.closed(k) for k in keys):
            self.alerts.clear_soon("providers_down")
        callable_entries = [e for e, k in zip(answer, keys, strict=True) if self.breakers.callable(k)]
        if not callable_entries:
            message = f"every answer entry ({len(answer)}) has an open breaker; questions go search-only"
            self.alerts.raise_soon("providers_down", message)
            return "models_unavailable"
        if not any(
            (e.paid and state == "normal") or (not e.paid and not self.quota.over_warn(e.provider))
            for e in callable_entries
        ):
            return "quota_exhausted"
        return None

    async def drain(self) -> None:
        """Wait for the background work (settles of cancelled calls, alerts, breaker rows)."""
        while self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.alerts.drain()
        await self.breakers.event_log.drain()

    async def close(self) -> None:
        await self.drain()
        for provider in self.providers.values():
            await provider.close()

    # -- one entry ----------------------------------------------------------------------------------------

    def _time_for(self, stage: StageName, deadline_at: float) -> float:
        timeout: float = getattr(self.limits, f"timeout_{stage}")
        return min(timeout, deadline_at - asyncio.get_running_loop().time())

    def _out_of_time(self, deadline_at: float) -> bool:
        return deadline_at - asyncio.get_running_loop().time() <= 0

    async def _take(
        self, stage: StageName, entry: Entry, chars: int, deadline_at: float, spend: RequestSpend
    ) -> _Ticket | None:
        """A ticket to call the entry now, or None to skip it (no time, open breaker, quota, budget)."""
        key = (entry.provider, entry.model)
        if self._out_of_time(deadline_at) or not self.breakers.callable(key):
            return None
        ticket: _Ticket | None
        if not entry.paid:
            if self.quota.over_warn(entry.provider):
                log.info("model_entry_skipped", stage=stage, model=entry.model, why="quota")
                return None
            ticket = _Ticket(stage, entry, 0.0, None, spend)
        else:
            ticket = await self._reserve(stage, entry, chars, spend)
            if ticket is None:
                return None
        if not self.breakers.allow(key):  # another call took the half-open trial meanwhile
            await self._settle(ticket, 0.0)
            return None
        return ticket

    async def _reserve(
        self, stage: StageName, entry: Entry, chars: int, spend: RequestSpend
    ) -> _Ticket | None:
        assert entry.price is not None  # the config requires a price on a paid entry
        prompt_tokens = math.ceil(chars / CHARS_PER_TOKEN)
        max_tokens = self.config.stage(stage).max_tokens
        estimate = (
            prompt_tokens * entry.price.input_per_mtok + max_tokens * entry.price.output_per_mtok
        ) / 1e6
        if not spend.fits(estimate):
            log.info("model_entry_skipped", stage=stage, model=entry.model, why="request_cap")
            return None
        day = self.clock().date()
        try:
            reserved = await self.budget.reserve(day, estimate, self.limits.daily_budget_usd)
        except StoreUnavailable as exc:  # no reservation, no paid call
            log.error("budget_reserve_failed", error=str(exc))
            return None
        if not reserved:
            log.info("model_entry_skipped", stage=stage, model=entry.model, why="budget")
            return None
        spend.spent += estimate
        return _Ticket(stage, entry, estimate, day, spend)

    async def _close(
        self, ticket: _Ticket, exc: BaseException | None, outcome: _Outcome | None = None
    ) -> float:
        """Record the verdict on the breaker and the quota, settle the reservation; returns the cost."""
        entry = ticket.entry
        key = (entry.provider, entry.model)
        if exc is None or isinstance(exc, _SchemaMiss):
            self.breakers.success(key)
            if isinstance(exc, _SchemaMiss):
                log.warning("model_schema_miss", stage=ticket.stage, model=entry.model)
        elif isinstance(exc, RateLimited):
            self.breakers.failure(key, rate_limited=True)
        elif isinstance(exc, ContextTooLong):
            log.error("context_too_long", stage=ticket.stage, model=entry.model)
            self.breakers.release(key)
        elif isinstance(exc, ProviderError | TimeoutError):
            self.breakers.failure(key)
        else:  # cancelled, or a bug: no verdict on the provider
            self.breakers.release(key)
        if isinstance(exc, ProviderError | TimeoutError):
            log.warning(
                "model_call_failed",
                stage=ticket.stage,
                model=entry.model,
                error=str(exc) or type(exc).__name__,
            )
        if not entry.paid:
            if not isinstance(exc, asyncio.CancelledError):
                self.quota.count(entry.provider, outcome.rate if outcome else None)
            return 0.0
        cost = 0.0
        if outcome is not None:
            assert entry.price is not None
            cost = (
                outcome.tokens_in * entry.price.input_per_mtok
                + outcome.tokens_out * entry.price.output_per_mtok
            ) / 1e6
        if isinstance(exc, asyncio.CancelledError | GeneratorExit):
            self._spawn(self._settle(ticket, cost))  # the caller is leaving: don't hold it on the database
        else:
            await self._settle(ticket, cost)
        return cost

    async def _settle(self, ticket: _Ticket, actual: float) -> None:
        ticket.spend.spent += actual - ticket.estimate
        if ticket.day is None:
            return
        lim = self.limits
        try:
            await self.budget.settle(
                ticket.day,
                actual - ticket.estimate,
                lim.budget_paid_cutoff * lim.daily_budget_usd,
                lim.daily_budget_usd,
                self.clock(),
            )
        except StoreUnavailable as exc:
            log.error("budget_settle_failed", error=str(exc))

    def _spawn(self, work: Awaitable[None]) -> None:
        async def run() -> None:
            await work

        task = asyncio.get_running_loop().create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
