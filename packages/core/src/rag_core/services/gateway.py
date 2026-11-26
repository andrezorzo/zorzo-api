"""Model gateway (ADR 0007, LLD "Components and dependencies"): every model call goes through a stage's chain.

Each chain entry is tried in order, within `min(TIMEOUT_<STAGE>, time left)`; an entry is skipped when its
breaker is open (LLD "6.6 Circuit breaker"), when it is free and its provider passed the quota warning (LLD
"6.8 Free quota"), or when it is paid and its estimate doesn't fit in what the day or the request has left
(LLD "6.2 Daily budget"). A failure falls to the next entry; an exhausted chain raises
`ModelsUnavailableError`.
"""

import asyncio
import contextlib
import math
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime

import structlog
from pydantic import BaseModel, ValidationError

from rag_core.background import BackgroundTasks
from rag_core.events import DegradedReason
from rag_core.limits import Limits
from rag_core.models_config import Entry, ModelsConfig, StageName
from rag_core.ports import (
    Alerter,
    Budget,
    ChatChunk,
    ContextTooLongError,
    ModelProvider,
    ProviderError,
    RateInfo,
    RateLimitedError,
    StoreUnavailableError,
)
from rag_core.services.alerts import Alerts
from rag_core.services.breaker import Breakers
from rag_core.services.quota import Quota

log = structlog.get_logger()

CHARS_PER_TOKEN = 3  # conservative for English and Portuguese, no tokenizer (LLD "6.2 Daily budget")
# A timeout counts toward the breaker when the call had at least this share of its stage timeout, whatever
# limit ended it (the stage timeout, the rerank chain's, the caller's deadline), and the model sent no text,
# or went silent for this share of it after its text (silent: no data frame; an aggregator's keep-alive is not
# the model). With less time it never had its chance, and an answer still coming is alive, only long (owner
# decision, LLD "6.6 Circuit breaker").
FAIR_SHARE = 0.5
# The background work gets this long at shutdown, its three kinds at once, plus a short wait for what it
# cancels (`CANCEL_WAIT_S`): Docker kills the container 10 s after SIGTERM.
SHUTDOWN_DRAIN_S = 6.0


class ModelsUnavailableError(Exception):
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


@dataclass
class _Ticket:
    """One call let through: its entry, and the reservation to settle (0 for a free entry)."""

    stage: StageName
    entry: Entry
    estimate: float
    day: date | None
    spend: RequestSpend
    deadline_at: float  # the caller's (inf when the caller bounds the call itself)
    chain_ends_at: float = math.inf  # a limit the gateway sets on the whole chain (rerank)
    trial: bool = False  # the breaker's half-open trial call
    # False when the call's time ran out for a reason that says nothing about the provider: it had less than
    # `FAIR_SHARE` of its stage timeout, or its answer had started and was still coming, or a slow consumer
    # held the time (see `FAIR_SHARE`).
    blame_timeout: bool = True


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
        self._timeouts: dict[StageName, float] = {
            "route": limits.timeout_route,
            "rerank": limits.timeout_rerank,
            "answer": limits.timeout_answer,
            "judge": limits.timeout_judge,
        }
        self._background = BackgroundTasks()

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
        chars = _prompt_chars(messages)
        max_tokens = self.config.stage(stage).max_tokens
        for entry in self.config.stage(stage).chain:
            # A schema miss is retried once on the same entry, then falls through, and never counts toward
            # the breaker: the provider worked, the model missed (LLD "6.6 Circuit breaker").
            for _ in range(2 if schema is not None else 1):
                ticket = await self._take(stage, entry, chars, deadline_at, spend)
                if ticket is None:
                    break
                try:
                    async with asyncio.timeout(self._time_for(ticket)):
                        result = await self.providers[entry.provider].chat(
                            entry.model, messages, max_tokens=max_tokens, schema=json_schema
                        )
                except BaseException as exc:
                    await self._close(ticket, exc)
                    if _failed(exc):
                        break
                    raise
                usage = Usage(result.tokens_in, result.tokens_out)
                cost = await self._close(ticket, None, usage, result.rate)
                # The model's miss, as in `stream`: never the breaker's, and not retried like a schema miss.
                if not result.text.strip():
                    log.warning("model_empty_answer", stage=stage, model=entry.model)
                    break
                try:
                    parsed = schema.model_validate_json(result.text) if schema is not None else None
                except ValidationError:
                    log.warning("model_schema_miss", stage=stage, model=entry.model)
                    continue
                return Completion(result.text, parsed, usage, cost, entry.provider, entry.model)
            if self._out_of_time(deadline_at):
                break
        raise ModelsUnavailableError(stage)

    async def stream(
        self, stage: StageName, messages: list[dict[str, str]], *, deadline_at: float, spend: RequestSpend
    ) -> AsyncIterator[str | Completion]:
        """Yields the answer's text as it comes, then its `Completion`.

        The answer starts with the model's first real text (leading whitespace is held back until then). A
        failure, or a stream that ends with no text, before it falls to the next entry; a failure after it
        raises `ModelsUnavailableError`, and the caller keeps the text it was given.
        """
        chars = _prompt_chars(messages)
        max_tokens = self.config.stage(stage).max_tokens
        for entry in self.config.stage(stage).chain:
            ticket = await self._take(stage, entry, chars, deadline_at, spend)
            if ticket is None:
                if self._out_of_time(deadline_at):
                    break
                continue
            parts: list[str] = []
            tokens_in = tokens_out = 0
            loop = asyncio.get_running_loop()
            ends_at = loop.time() + self._time_for(ticket)
            last_chunk_at = loop.time()  # the provider's silence is measured from here
            # The answer has started once the model sent real text: whitespace before it is held back, so
            # "started" means one thing for the visitor, the fallback and the breaker.
            wrote = False
            chunks: AsyncIterator[ChatChunk] | None = None
            try:
                chunks = aiter(
                    self.providers[entry.provider].chat_stream(entry.model, messages, max_tokens=max_tokens)
                )
                while True:
                    # Each read is bounded on its own, never across a `yield`: the consumer's time between
                    # chunks counts toward the end, but a timeout can't fire inside the consumer.
                    if loop.time() >= ends_at:
                        if wrote:  # the consumer held the time (it only gets it once there is text)
                            ticket.blame_timeout = False
                        raise TimeoutError
                    async with asyncio.timeout_at(ends_at):
                        chunk = await anext(chunks, None)
                    last_chunk_at = loop.time()
                    if chunk is None:
                        break
                    if chunk.tokens_in is not None:
                        tokens_in, tokens_out = chunk.tokens_in, chunk.tokens_out or 0
                    if chunk.text:
                        parts.append(chunk.text)
                        if wrote or chunk.text.strip():
                            text = chunk.text if wrote else "".join(parts)  # with the held whitespace
                            wrote = True
                            yield text
                            last_chunk_at = loop.time()  # the consumer's time isn't the provider's silence
            except BaseException as exc:
                if wrote and loop.time() - last_chunk_at < FAIR_SHARE * self._timeouts[stage]:
                    ticket.blame_timeout = False  # the answer was still coming: alive, only long
                if chunks is not None:
                    await _aclose(chunks)  # the provider's response, not left to the garbage collector
                await self._close(ticket, exc)
                if _failed(exc):
                    if wrote:
                        raise ModelsUnavailableError(f"{stage} failed after the first token") from exc
                    continue
                raise
            usage = Usage(tokens_in, tokens_out)
            cost = await self._close(ticket, None, usage)
            if not wrote:
                # The stream ended with no text (a content filter, the tokens spent on reasoning): the
                # provider worked, the model missed, so like a schema miss it never counts toward the breaker.
                log.warning("model_empty_answer", stage=stage, model=entry.model)
                continue
            yield Completion("".join(parts), None, usage, cost, entry.provider, entry.model)
            return
        raise ModelsUnavailableError(stage)

    async def rerank(self, query: str, documents: list[str], *, deadline_at: float = math.inf) -> list[float]:
        """Scores per document from the `rerank` stage; `ModelsUnavailableError` sends the caller to local.

        The whole chain gets one `TIMEOUT_RERANK`: the local level must still have time after it.
        """
        chain_ends_at = asyncio.get_running_loop().time() + self._timeouts["rerank"]
        chars = len(query) + sum(len(d) for d in documents)
        spend = RequestSpend(self.limits.request_cost_cap_usd)
        for entry in self.config.stage("rerank").chain:
            ticket = await self._take("rerank", entry, chars, deadline_at, spend, chain_ends_at)
            if ticket is None:
                continue
            try:
                async with asyncio.timeout(self._time_for(ticket)):
                    result = await self.providers[entry.provider].rerank(entry.model, query, documents)
            except BaseException as exc:
                await self._close(ticket, exc)
                if _failed(exc):
                    continue
                raise
            await self._close(ticket, None, Usage(math.ceil(chars / CHARS_PER_TOKEN), 0), result.rate)
            return result.scores
        raise ModelsUnavailableError("rerank")

    async def mode(self) -> DegradedReason | None:
        """None when questions can be answered by the models now; otherwise why they go search-only.

        Raises `StoreUnavailableError` when the budget row can't be read.
        """
        state = await self.budget.state(self.clock().date())
        # The alert opens when the day's paid entries stop (the cutoff), since reservations keep the spend
        # from reaching the cap itself; it closes on a new UTC day (LLD "6.10 Alerts").
        if state == "normal":
            self.alerts.clear_soon("budget_exhausted")
        else:
            cap = self.limits.daily_budget_usd
            self.alerts.raise_soon(
                "budget_exhausted",
                f"today's paid budget (US${cap}) reached the cutoff; paid entries are off until 00:00 UTC"
                if state == "paid_disabled"
                else f"today's budget of US${cap} is spent; questions go search-only",
                severity=0 if state == "paid_disabled" else 1,  # search-only: told within a minute
            )
        if state == "degraded":
            return "budget_exhausted"
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

    async def drain(self, timeout: float | None = None) -> None:
        """Wait for the background work (settles of cancelled calls, alerts, breaker rows), all of it within
        `timeout`."""
        # At once, each with the whole time: one kind stuck on the database can't take the others' time.
        drains = (self._background.drain, self.alerts.drain, self.breakers.event_log.drain)
        results = await asyncio.gather(*(drain(timeout) for drain in drains), return_exceptions=True)
        for name, result in zip(("settles", "alerts", "breaker_rows"), results, strict=True):
            if isinstance(result, BaseException):  # a bug in one drain must not cut the others short
                log.error("drain_failed", work=name, error=type(result).__name__)

    async def close(self) -> None:
        """At shutdown: the background work, then every client the gateway owns (providers, alerter)."""
        await self.drain(SHUTDOWN_DRAIN_S)
        clients: list[ModelProvider | Alerter] = [*self.providers.values(), self.alerts.alerter]
        for client in clients:
            try:  # one client failing to close doesn't keep the others open
                await client.close()
            except Exception as exc:
                log.warning("client_close_failed", client=type(client).__name__, error=type(exc).__name__)

    # -- one entry ----------------------------------------------------------------------------------------

    def _time_for(self, ticket: _Ticket) -> float:
        """`min(TIMEOUT_<STAGE>, time left)`; notes on the ticket whether its timeout is the provider's."""
        now = asyncio.get_running_loop().time()
        timeout = self._timeouts[ticket.stage]
        granted = min(now + timeout, ticket.chain_ends_at, ticket.deadline_at) - now
        ticket.blame_timeout = granted >= FAIR_SHARE * timeout
        return granted

    def _out_of_time(self, deadline_at: float) -> bool:
        return deadline_at - asyncio.get_running_loop().time() <= 0

    async def _take(
        self,
        stage: StageName,
        entry: Entry,
        chars: int,
        deadline_at: float,
        spend: RequestSpend,
        chain_ends_at: float = math.inf,
    ) -> _Ticket | None:
        """A ticket to call the entry now, or None to skip it (no time, open breaker, quota, budget)."""
        key = (entry.provider, entry.model)
        if self._out_of_time(min(deadline_at, chain_ends_at)) or not self.breakers.callable(key):
            return None
        if not entry.paid and self.quota.over_warn(entry.provider):
            log.info("model_entry_skipped", stage=stage, model=entry.model, why="quota")
            return None
        if not self.breakers.allow(key):  # another call took the half-open trial meanwhile
            return None
        trial = not self.breakers.closed(key)
        ticket = _Ticket(stage, entry, 0.0, None, spend, deadline_at, chain_ends_at, trial)
        try:
            reserved = not entry.paid or await self._reserve(ticket, chars)
        except BaseException:
            self.breakers.release(key, trial)  # cancelled while reserving: the trial must not stay taken
            raise
        if not reserved:
            self.breakers.release(key, trial)
            return None
        return ticket

    async def _reserve(self, ticket: _Ticket, chars: int) -> bool:
        """Reserve the paid entry's estimate against the request and the day; False skips the entry."""
        stage, entry, spend = ticket.stage, ticket.entry, ticket.spend
        assert entry.price is not None  # the config requires a price on a paid entry
        prompt_tokens = math.ceil(chars / CHARS_PER_TOKEN)
        max_tokens = self.config.stage(stage).max_tokens
        estimate = (
            prompt_tokens * entry.price.input_per_mtok + max_tokens * entry.price.output_per_mtok
        ) / 1e6
        if not spend.fits(estimate):
            log.info("model_entry_skipped", stage=stage, model=entry.model, why="request_cap")
            return False
        day = self.clock().date()
        # Shielded: a caller that leaves mid-reserve must not leave a committed reservation nobody settles.
        reserving = asyncio.ensure_future(self.budget.reserve(day, estimate, self.limits.daily_budget_usd))
        try:
            reserved = await asyncio.shield(reserving)
        except asyncio.CancelledError:
            self._background.spawn(self._release_if_reserved(reserving, day, estimate))
            raise
        except StoreUnavailableError as exc:  # no reservation, no paid call
            log.error("budget_reserve_failed", error=str(exc))
            return False
        if not reserved:
            log.info("model_entry_skipped", stage=stage, model=entry.model, why="budget")
            return False
        spend.spent += estimate
        ticket.estimate, ticket.day = estimate, day
        return True

    async def _close(
        self,
        ticket: _Ticket,
        exc: BaseException | None,
        usage: Usage | None = None,
        rate: RateInfo | None = None,
    ) -> float:
        """Record the verdict on the breaker and the quota, settle the reservation; returns the cost."""
        entry = ticket.entry
        key, trial = (entry.provider, entry.model), ticket.trial
        # A timeout counts toward the breaker only when `_time_for` said it is the provider's (LLD "6.6").
        if exc is None:
            self.breakers.success(key, trial)
        elif isinstance(exc, RateLimitedError):
            self.breakers.failure(key, rate_limited=True, trial=trial)
        elif isinstance(exc, ContextTooLongError):
            log.error("context_too_long", stage=ticket.stage, model=entry.model)
            self.breakers.release(key, trial)
        elif _failed(exc) and not (isinstance(exc, TimeoutError) and not ticket.blame_timeout):
            self.breakers.failure(key, trial=trial)
        else:  # a timeout that isn't the provider's, cancelled, or a bug: no verdict on the provider
            self.breakers.release(key, trial)
        if _failed(exc):
            log.warning(
                "model_call_failed",
                stage=ticket.stage,
                model=entry.model,
                error=str(exc) or type(exc).__name__,
            )
        if not entry.paid:
            if not isinstance(exc, asyncio.CancelledError):
                self.quota.count(entry.provider, rate)
            return 0.0
        # A call the provider refused settles with actual 0 (LLD "6.2 Daily budget"). One that may have run on
        # the provider's side (a timeout, or the visitor leaving mid-call) keeps its estimate: refunding it
        # would let a visitor who disconnects spend past the cap unseen.
        # A refusal comes with the status, before any text, so a stream that gave text is never refused.
        cost = 0.0 if isinstance(exc, ProviderError) and exc.refused else ticket.estimate
        if exc is None:
            assert entry.price is not None
            if usage is None or usage.tokens_in + usage.tokens_out == 0:
                cost = ticket.estimate  # billed, but the provider sent no usage: keep the estimate
            else:
                cost = (
                    usage.tokens_in * entry.price.input_per_mtok
                    + usage.tokens_out * entry.price.output_per_mtok
                ) / 1e6
        if isinstance(exc, asyncio.CancelledError | GeneratorExit):
            # The caller is leaving: don't hold it on the database.
            self._background.spawn(self._settle(ticket, cost))
        else:
            await self._settle(ticket, cost)
        return cost

    async def _release_if_reserved(
        self, reserving: "asyncio.Future[bool]", day: date, estimate: float
    ) -> None:
        """A reservation its caller left before it answered: give the estimate back if it was taken."""
        try:
            # Shielded: a shutdown cancelling this must not cut the reserve's commit.
            if await asyncio.shield(reserving):
                await self._settle_row(day, -estimate)
        except StoreUnavailableError:
            return
        except asyncio.CancelledError:
            # Shutdown ran out of time: the day's row may keep this estimate. Said, so the gap is explained.
            event = (
                "reservation_outcome_unknown" if reserving.cancelled() else "reservation_release_abandoned"
            )
            log.warning(event, day=str(day), estimate=estimate)
            reserving.add_done_callback(lambda f: f.cancelled() or f.exception())
            raise

    async def _settle(self, ticket: _Ticket, actual: float) -> None:
        ticket.spend.spent += actual - ticket.estimate
        if ticket.day is not None:
            await self._settle_row(ticket.day, actual - ticket.estimate)

    async def _settle_row(self, day: date, delta: float) -> None:
        lim = self.limits
        try:
            await self.budget.settle(
                day, delta, lim.budget_paid_cutoff * lim.daily_budget_usd, lim.daily_budget_usd, self.clock()
            )
        except StoreUnavailableError as exc:
            log.error("budget_settle_failed", error=str(exc))


async def _aclose(chunks: AsyncIterator[object]) -> None:
    if isinstance(chunks, AsyncGenerator):
        with contextlib.suppress(Exception):
            await chunks.aclose()


def _prompt_chars(messages: list[dict[str, str]]) -> int:
    return sum(len(m.get("content", "")) for m in messages)


def _failed(exc: BaseException | None) -> bool:
    """A provider failure, which falls to the next entry; anything else (cancelled, a bug) propagates."""
    return isinstance(exc, ProviderError | TimeoutError)
