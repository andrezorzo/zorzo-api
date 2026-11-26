"""The gate (spec, "Modules built"): what a question must pass before the stream opens.

In order: the bot check, the question's normalization, the rate limit (LLD "Security", "6.7 Rate limit").
Each failure is an `ApiError`, answered as a plain HTTP error. The budget is not a refusal: after the gate,
the route asks the model gateway for the mode, and a spent budget answers search-only.
"""

import asyncio
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from uuid import UUID

import structlog

from rag_core.errors import ApiError, ErrorCode
from rag_core.limits import Limits
from rag_core.ports import BotCheck, Bucket, RateLimiter, StoreUnavailable
from rag_core.services.client_ip import hashed_ip, viewer_ip
from rag_core.services.question import normalize

log = structlog.get_logger()


@dataclass(frozen=True)
class Asker:
    """Who is asking, as the request shows it; the raw address never leaves the gate."""

    session: UUID
    viewer_address: str | None  # CloudFront's header
    peer: str | None  # the socket's address


class Gate:
    def __init__(
        self,
        bot_check: BotCheck,
        limiter: RateLimiter,
        limits: Limits,
        salt_seed: bytes,
        app_env: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.clock = clock  # the UTC day picks the salt and the day buckets
        self.bot_check = bot_check
        self.limiter = limiter
        self.limits = limits
        self.salt_seed = salt_seed
        self.app_env = app_env

    async def check(self, question: str, token: str, asker: Asker, deadline_at: float) -> str:
        """The normalized question, or `ApiError`: 403, 422, 429, or 503 (no viewer address in production, the
        database down, or the deadline reached).

        `deadline_at` is the request's deadline on the event loop's clock; the gate never outlives it.
        """
        step = ["bot_check"]  # the step running, for the log line when the deadline cuts it
        try:
            async with asyncio.timeout_at(deadline_at) as scope:
                return await self._check(question, token, asker, step)
        except TimeoutError as exc:
            if not scope.expired():
                raise  # a timeout of some step's own, not the request's deadline
            # A stuck database is an outage; a slow provider is only slow.
            (log.error if step[0] == "rate_limit" else log.warning)("deadline_exceeded", stage=step[0])
            raise ApiError(ErrorCode.UNAVAILABLE) from exc

    async def _check(self, question: str, token: str, asker: Asker, step: list[str]) -> str:
        if not await self.bot_check.verify(token):
            raise ApiError(ErrorCode.BOT_CHECK_FAILED)
        text = normalize(question, self.limits)
        now = self.clock()
        ip = hashed_ip(viewer_ip(asker.viewer_address, asker.peer, self.app_env), self.salt_seed, now.date())
        buckets = self._buckets(ip, asker.session, now)
        step[0] = "rate_limit"
        try:
            refused = await self.limiter.take(buckets, now)
        except StoreUnavailable as exc:  # a database down is `unavailable` (LLD "Errors")
            log.error("rate_limit_failed", error=str(exc))
            raise ApiError(ErrorCode.UNAVAILABLE) from exc
        if refused is not None:
            wait = max((seconds for _, seconds in refused), default=0.0)
            log.warning("rate_limited", buckets=sorted(b.kind for b, _ in refused))
            # Whole seconds, at least one: a refill that is due now still needs a new request.
            raise ApiError(ErrorCode.RATE_LIMITED, headers={"Retry-After": str(max(1, math.ceil(wait)))})
        return text

    def _buckets(self, ip: str, session: UUID, now: datetime) -> list[Bucket]:
        lim = self.limits
        refill_ip = lim.rate_ip_burst / lim.rate_ip_window
        refill_session = lim.rate_session_burst / lim.rate_ip_window  # one window for both (LLD "Values")
        day = now.date()
        midnight = datetime.combine(day + timedelta(days=1), time(), UTC)
        return [
            Bucket(f"ip:{ip}:burst", "ip_burst", lim.rate_ip_burst, refill_ip),
            Bucket(f"ip:{ip}:{day}", "ip_day", lim.rate_ip_daily, 0.0, midnight),
            Bucket(f"session:{session}:burst", "session_burst", lim.rate_session_burst, refill_session),
            Bucket(f"session:{session}:{day}", "session_day", lim.rate_session_daily, 0.0, midnight),
        ]
