"""Free quota per provider and UTC day (LLD "6.8 Free quota"), in the API process's memory.

The provider's `x-ratelimit-*` headers win when a response has them; otherwise a local count of this
process's free calls against `daily_requests`. A restart forgets the count: the provider's 429 then opens the
breaker, so the cost is one failed call.
"""

import time
from collections.abc import Callable
from datetime import UTC, date, datetime

from rag_core.limits import Limits
from rag_core.models_config import ModelsConfig
from rag_core.ports import RateInfo

# Headers can count per minute (OpenAI-style) or per day: what they said is trusted only this long, then a
# call is let through again and its headers update it.
REPORTED_TTL_S = 60.0


class Quota:
    def __init__(
        self,
        config: ModelsConfig,
        limits: Limits,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.limits = limits
        self.clock = clock
        self.monotonic = monotonic
        self._day: date | None = None
        self._used: dict[str, int] = {}
        self._reported: dict[str, tuple[RateInfo, float]] = {}  # with when it was reported

    def _roll(self) -> None:
        today = self.clock().date()
        if today != self._day:
            self._day, self._used, self._reported = today, {}, {}

    def over_warn(self, provider: str) -> bool:
        """Whether the provider's free calls today passed `QUOTA_WARN` of its daily limit."""
        self._roll()
        found = self._reported.get(provider)
        if found is not None and self.monotonic() - found[1] < REPORTED_TTL_S:
            reported = found[0]
            used, limit = reported.limit - reported.remaining, reported.limit
        else:
            used, limit = self._used.get(provider, 0), self.config.providers[provider].daily_requests or 0
        return limit > 0 and used >= self.limits.quota_warn * limit

    def count(self, provider: str, rate: RateInfo | None) -> None:
        """One free call made; `rate` is what the response's headers said, when they did."""
        self._roll()
        self._used[provider] = self._used.get(provider, 0) + 1
        if rate is not None and rate.limit > 0:
            self._reported[provider] = (rate, self.monotonic())
