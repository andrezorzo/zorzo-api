"""Model gateway (ADR 0007, ADR 0016, LLD "Components and dependencies"): every model call goes through a
stage's chain.

The gateway walks the chain: each free entry not cooling down, through the LiteLLM Router (timeouts and
cooldowns: LLD "6.6 Circuit breaker"); then the paid entry, if its estimate fits in what the day and the
request have left (LLD "6.2 Daily budget"): reserved in Postgres before the call, settled after it. Every
call runs under the caller's deadline; an exhausted chain raises `ModelsUnavailableError`.
"""

import asyncio
import contextlib
import math
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime

import structlog
from pydantic import BaseModel, ValidationError

from rag_core.background import BackgroundTasks
from rag_core.events import DegradedReason
from rag_core.limits import Limits
from rag_core.models_config import Entry, ModelsConfig, StageName, entry_id
from rag_core.ports import (
    Budget,
    ChatResult,
    ContextTooLongError,
    Cooldown,
    EventLog,
    ModelRouter,
    ProviderError,
    StoreUnavailableError,
)
from rag_core.services.alerts import Alerts

log = structlog.get_logger()

CHARS_PER_TOKEN = 3  # conservative for English and Portuguese, no tokenizer (LLD "6.2 Daily budget")
# An empty answer or a schema miss is the model's, not the provider's: a free entry gets one more try before
# the chain moves on, and a miss never cools an entry (LLD "6.6 Circuit breaker").
ATTEMPTS_PER_FREE_ENTRY = 2
RATE_LIMITED = "429"
# The estimate already assumes the longest answer, so a settle past this multiple of it is a provider's bug
# (a unit change, a broken count), and the estimate is kept instead (LLD "6.2 Daily budget").
MAX_COST_OVER_ESTIMATE = 2
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
class _Outcome:
    """How one stream ended: with an answer, with the model's miss (neither field set), or failed."""

    completion: Completion | None = None
    failed: bool = False


@dataclass
class _Reservation:
    """The paid entry's estimate, reserved against the request and the day."""

    stage: StageName
    entry: Entry
    estimate: float
    day: date
    spend: RequestSpend


class ModelGateway:
    def __init__(
        self,
        config: ModelsConfig,
        models: ModelRouter,
        budget: Budget,
        alerts: Alerts,
        event_log: EventLog,
        limits: Limits,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.config = config
        self.models = models
        self.budget = budget
        self.alerts = alerts
        self.event_log = event_log
        self.limits = limits
        self.clock = clock
        self._timeouts: dict[StageName, float] = {
            "route": limits.timeout_route,
            "rerank": limits.timeout_rerank,
            "answer": limits.timeout_answer,
            "judge": limits.timeout_judge,
        }
        self._cooling: dict[str, Cooldown] = {}
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
        chain = self.config.stage(stage)
        await self._note_cooldowns()
        for entry in chain.free:
            for _ in range(ATTEMPTS_PER_FREE_ENTRY):
                if not self._callable(stage, entry, deadline_at):
                    break
                try:
                    async with asyncio.timeout_at(deadline_at):
                        result = await self.models.complete(
                            entry_id(stage, entry),
                            messages,
                            max_tokens=chain.max_tokens,
                            schema=json_schema,
                            timeout=self._time_for(stage, deadline_at),
                        )
                except (ProviderError, TimeoutError) as exc:
                    self._failed(stage, exc)
                    break
                finally:
                    await self._note_cooldowns()
                completion = self._completion(stage, entry, result, schema, cost=0.0)
                if completion is not None:
                    return completion
        reservation = await self._paid_reservation(stage, messages, deadline_at, spend)
        if reservation is not None:
            paid_result = await self._paid_call(
                reservation,
                self.models.complete(
                    entry_id(stage, reservation.entry),
                    messages,
                    max_tokens=chain.max_tokens,
                    schema=json_schema,
                    timeout=self._time_for(stage, deadline_at),
                ),
                deadline_at,
            )
            if paid_result is not None:
                cost = await self._settle_success(reservation, paid_result)
                completion = self._completion(stage, reservation.entry, paid_result, schema, cost)
                if completion is not None:
                    return completion
        raise ModelsUnavailableError(stage)

    async def stream(
        self, stage: StageName, messages: list[dict[str, str]], *, deadline_at: float, spend: RequestSpend
    ) -> AsyncIterator[str | Completion]:
        """Yields the answer's text as it comes, then its `Completion`.

        The answer starts with the model's first real text (leading whitespace is held back until then). A
        failure, or a stream that ends with no text, before it falls to the next entry; a failure after it
        raises `ModelsUnavailableError`, and the caller keeps the text it was given.
        """
        chain = self.config.stage(stage)
        await self._note_cooldowns()
        for entry in chain.free:
            for _ in range(ATTEMPTS_PER_FREE_ENTRY):
                if not self._callable(stage, entry, deadline_at):
                    break
                chunks = self.models.stream(
                    entry_id(stage, entry),
                    messages,
                    max_tokens=chain.max_tokens,
                    timeout=self._time_for(stage, deadline_at),
                )
                outcome = _Outcome()
                try:
                    async for text in self._relay(
                        stage, entry, chunks, deadline_at, outcome, reservation=None
                    ):
                        yield text
                finally:
                    await self._note_cooldowns()
                if outcome.completion is not None:
                    yield outcome.completion
                    return
                if outcome.failed:  # the provider's failure: next entry; a model miss gets another try
                    break
        reservation = await self._paid_reservation(stage, messages, deadline_at, spend)
        if reservation is not None:
            chunks = self.models.stream(
                entry_id(stage, reservation.entry),
                messages,
                max_tokens=chain.max_tokens,
                timeout=self._time_for(stage, deadline_at),
            )
            outcome = _Outcome()
            try:
                async for text in self._relay(
                    stage, reservation.entry, chunks, deadline_at, outcome, reservation
                ):
                    yield text
            finally:
                await self._note_cooldowns()
            if outcome.completion is not None:
                yield outcome.completion
                return
        raise ModelsUnavailableError(stage)

    async def rerank(self, query: str, documents: list[str], *, deadline_at: float = math.inf) -> list[float]:
        """Scores per document from the `rerank` stage; `ModelsUnavailableError` sends the caller to local.

        The whole stage gets one `TIMEOUT_RERANK`: the local level must still have time after it.
        """
        ends_at = min(deadline_at, asyncio.get_running_loop().time() + self._timeouts["rerank"])
        await self._note_cooldowns()
        for entry in self.config.stage("rerank").free:
            if not self._callable("rerank", entry, ends_at):
                continue
            try:
                async with asyncio.timeout_at(ends_at):
                    return await self.models.rerank(
                        entry_id("rerank", entry), query, documents, timeout=self._time_for("rerank", ends_at)
                    )
            except (ProviderError, TimeoutError) as exc:
                self._failed("rerank", exc)
            finally:
                await self._note_cooldowns()
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
        await self._note_cooldowns()
        answer = self.config.stage("answer")
        cooling = [self._cooling.get(entry_id("answer", e)) for e in answer.free]
        all_cooling = bool(cooling) and all(c is not None for c in cooling)
        if not all_cooling:
            self.alerts.clear_soon("providers_down")
        else:
            message = f"every free answer entry ({len(answer.free)}) is cooling down"
            self.alerts.raise_soon("providers_down", message)
        paid_can_run = (
            answer.paid is not None
            and state == "normal"
            and entry_id("answer", answer.paid) not in self._cooling
        )
        if (all_cooling or not answer.free) and not paid_can_run:
            limited = all_cooling and all(c is not None and c.status == RATE_LIMITED for c in cooling)
            return "quota_exhausted" if limited else "models_unavailable"
        return None

    async def drain(self, timeout: float | None = None) -> None:
        """Wait for the background work (settles of cancelled calls, alerts, cooldown rows), all of it within
        `timeout`."""
        # At once, each with the whole time: one kind stuck on the database can't take the others' time.
        drains = (self._background.drain, self.alerts.drain, self.event_log.drain)
        results = await asyncio.gather(*(drain(timeout) for drain in drains), return_exceptions=True)
        for name, result in zip(("settles", "alerts", "cooldown_rows"), results, strict=True):
            if isinstance(result, BaseException):  # a bug in one drain must not cut the others short
                log.error("drain_failed", work=name, error=type(result).__name__)

    async def close(self) -> None:
        """At shutdown: the background work, then every client the gateway owns (models, alerter)."""
        await self.drain(SHUTDOWN_DRAIN_S)
        for client in (self.models, self.alerts.alerter):
            try:  # one client failing to close doesn't keep the other open
                await client.close()
            except Exception as exc:
                log.warning("client_close_failed", client=type(client).__name__, error=type(exc).__name__)

    # -- calls --------------------------------------------------------------------------------------------

    def _callable(self, stage: StageName, entry: Entry, deadline_at: float) -> bool:
        """Time is left and the entry isn't cooling down."""
        return not self._out_of_time(deadline_at) and entry_id(stage, entry) not in self._cooling

    async def _relay(
        self,
        stage: StageName,
        entry: Entry,
        chunks: AsyncIterator[str | ChatResult],
        deadline_at: float,
        outcome: _Outcome,
        reservation: _Reservation | None,
    ) -> AsyncIterator[str]:
        """Yields one stream's text and records in `outcome` how it ended.

        A failure before the text falls through (`outcome.failed`); after it, raises `ModelsUnavailableError`.
        Each read is bounded by the deadline on its own, never across a `yield`.
        """
        held: list[str] = []  # leading whitespace, held back until real text arrives
        wrote = False
        result = ChatResult("", 0, 0, billable_counts=False)
        try:
            while True:
                async with asyncio.timeout_at(deadline_at):
                    piece = await anext(chunks, None)
                if piece is None:
                    break
                if isinstance(piece, ChatResult):
                    result = piece
                elif wrote:
                    yield piece
                else:
                    held.append(piece)
                    if piece.strip():
                        wrote = True
                        yield "".join(held)
        except BaseException as exc:
            await _aclose(chunks)
            if reservation is not None:
                await self._settle_failure(reservation, exc)
            if isinstance(exc, ProviderError | TimeoutError):
                self._failed(stage, exc)
                if wrote:
                    raise ModelsUnavailableError(f"{stage} failed after the first token") from exc
                outcome.failed = True
                return
            raise
        cost = 0.0 if reservation is None else await self._settle_success(reservation, result)
        if not wrote:
            # The stream ended with no text (a content filter, the tokens spent on reasoning): the model's
            # miss, which falls through like a schema miss.
            log.warning("model_empty_answer", stage=stage, model=entry.model)
            return
        usage = Usage(result.tokens_in, result.tokens_out)
        outcome.completion = Completion(result.text, None, usage, cost, entry.provider, entry.model)

    async def _paid_reservation(
        self, stage: StageName, messages: list[dict[str, str]], deadline_at: float, spend: RequestSpend
    ) -> _Reservation | None:
        """The paid entry's reservation, when the stage has one, time is left and it isn't cooling down."""
        paid = self.config.stage(stage).paid
        if paid is None or not self._callable(stage, paid, deadline_at):
            return None
        return await self._reserve(stage, paid, _prompt_chars(messages), spend)

    async def _paid_call(
        self, reservation: _Reservation, call: Awaitable[ChatResult], deadline_at: float
    ) -> ChatResult | None:
        """Await the paid call; settle it on failure (None) and leave the success to the caller."""
        try:
            async with asyncio.timeout_at(deadline_at):
                result = await call
        except BaseException as exc:
            await self._settle_failure(reservation, exc)
            if isinstance(exc, ProviderError | TimeoutError):
                self._failed(reservation.stage, exc)
                return None
            raise
        finally:
            await self._note_cooldowns()
        return result

    def _completion(
        self, stage: StageName, entry: Entry, result: ChatResult, schema: type[BaseModel] | None, cost: float
    ) -> Completion | None:
        """The answer, or None for the model's miss (an empty answer, a schema miss)."""
        if not result.text.strip():
            log.warning("model_empty_answer", stage=stage, model=entry.model)
            return None
        try:
            parsed = schema.model_validate_json(result.text) if schema is not None else None
        except ValidationError:
            log.warning("model_schema_miss", stage=stage, model=entry.model)
            return None
        usage = Usage(result.tokens_in, result.tokens_out)
        return Completion(result.text, parsed, usage, cost, entry.provider, entry.model)

    def _failed(self, stage: StageName, exc: BaseException) -> None:
        if isinstance(exc, ContextTooLongError):
            log.error("context_too_long", stage=stage)
        else:
            log.warning("model_call_failed", stage=stage, error=str(exc) or type(exc).__name__)

    def _time_for(self, stage: StageName, deadline_at: float) -> float:
        """`min(TIMEOUT_<STAGE>, time left)`."""
        return min(self._timeouts[stage], deadline_at - asyncio.get_running_loop().time())

    def _out_of_time(self, deadline_at: float) -> bool:
        return deadline_at - asyncio.get_running_loop().time() <= 0

    async def _note_cooldowns(self) -> None:
        """Refresh the cooldowns and write the ones that started or ended since the last look (LLD "6.6
        Circuit breaker"). A cooldown that ended and started again in between counts as both; one renewed
        before it ended (LiteLLM restarts it on a later failure) is the same cooldown."""
        try:
            now = await self.models.cooldowns()
        except Exception as exc:  # a bookkeeping failure never fails a question
            log.warning("cooldowns_unreadable", error=type(exc).__name__)
            return
        restarted = {
            name
            for name, old in self._cooling.items()
            if name in now and now[name].started_at >= old.started_at + old.length_s
        }
        for name in self._cooling:
            if name not in now or name in restarted:
                self.event_log.write("info", "gateway", "cooldown_ended", {"entry": name})
        for name, cooldown in now.items():
            if name not in self._cooling or name in restarted:
                fields = {"entry": name, "status": cooldown.status, "seconds": cooldown.length_s}
                self.event_log.write("warning", "gateway", "cooldown_started", fields)
        self._cooling = now

    # -- the paid entry's budget --------------------------------------------------------------------------

    async def _reserve(
        self, stage: StageName, entry: Entry, chars: int, spend: RequestSpend
    ) -> _Reservation | None:
        """Reserve the paid entry's estimate against the request and the day; None skips the entry."""
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
        # Shielded: a caller that leaves mid-reserve must not leave a committed reservation nobody settles.
        reserving = asyncio.ensure_future(self.budget.reserve(day, estimate, self.limits.daily_budget_usd))
        try:
            reserved = await asyncio.shield(reserving)
        except asyncio.CancelledError:
            self._background.spawn(self._release_if_reserved(reserving, day, estimate))
            raise
        except StoreUnavailableError as exc:  # no reservation, no paid call
            log.error("budget_reserve_failed", error=str(exc))
            return None
        if not reserved:
            log.info("model_entry_skipped", stage=stage, model=entry.model, why="budget")
            return None
        spend.spent += estimate
        return _Reservation(stage, entry, estimate, day, spend)

    async def _settle_success(self, reservation: _Reservation, result: ChatResult) -> float:
        """Settle a paid answer: the cost the provider reported, else its usage at the configured price, else
        the estimate (billed, but no usage the provider counted came)."""
        price = reservation.entry.price
        assert price is not None
        if result.cost_usd is not None:
            cost = result.cost_usd
        elif not result.billable_counts or result.tokens_in + result.tokens_out == 0:
            cost = reservation.estimate
        else:
            cost = (result.tokens_in * price.input_per_mtok + result.tokens_out * price.output_per_mtok) / 1e6
        if cost > MAX_COST_OVER_ESTIMATE * reservation.estimate:
            log.warning("implausible_cost", stage=reservation.stage, cost=cost, estimate=reservation.estimate)
            cost = reservation.estimate
        await self._settle(reservation, cost)
        return cost

    async def _settle_failure(self, reservation: _Reservation, exc: BaseException) -> None:
        """A call the provider refused settles at 0 (LLD "6.2 Daily budget"). One that may have run on the
        provider's side (a timeout, a 5xx, the visitor leaving mid-call) keeps its estimate: refunding it
        would let a visitor who disconnects spend past the cap unseen."""
        cost = 0.0 if isinstance(exc, ProviderError) and exc.refused else reservation.estimate
        if isinstance(exc, asyncio.CancelledError | GeneratorExit):
            # The caller is leaving: don't hold it on the database.
            self._background.spawn(self._settle(reservation, cost))
        else:
            await self._settle(reservation, cost)

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

    async def _settle(self, reservation: _Reservation, actual: float) -> None:
        reservation.spend.spent += actual - reservation.estimate
        await self._settle_row(reservation.day, actual - reservation.estimate)

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
