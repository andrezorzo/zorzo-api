"""Circuit breakers, one per chain entry (LLD "6.6 Circuit breaker"), in the API process's memory.

A breaker opens after `BREAKER_FAILURES` failures within `BREAKER_WINDOW`, or at once on a 429; after
`BREAKER_COOLDOWN` it lets exactly one trial call through (half-open), which closes or reopens it. Every
transition goes to `log_events` through the EventLog and to the log.
"""

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import structlog

from rag_core.limits import Limits
from rag_core.ports import EventLog

log = structlog.get_logger()

Key = tuple[str, str]  # (provider, model)


@dataclass
class _Breaker:
    state: Literal["closed", "open", "half_open"] = "closed"
    failures: deque[float] = field(default_factory=deque)  # times of the recent failures, while closed
    opened_at: float = 0.0
    trial: bool = False  # a half-open trial call is running


class Breakers:
    def __init__(
        self, limits: Limits, event_log: EventLog, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limits = limits
        self.event_log = event_log
        self.clock = clock
        self._breakers: dict[Key, _Breaker] = {}

    def _get(self, key: Key) -> _Breaker:
        return self._breakers.setdefault(key, _Breaker())

    def _cooled(self, b: _Breaker) -> bool:
        return self.clock() - b.opened_at >= self.limits.breaker_cooldown

    def callable(self, key: Key) -> bool:
        """Whether a call would be let through now; changes nothing."""
        b = self._get(key)
        if b.state == "closed":
            return True
        if b.state == "open":
            return self._cooled(b)
        return not b.trial

    def closed(self, key: Key) -> bool:
        return self._get(key).state == "closed"

    def allow(self, key: Key) -> bool:
        """Let a call through or not; past the cooldown, the call let through is the half-open trial."""
        b = self._get(key)
        if b.state == "open" and self._cooled(b):
            b.state = "half_open"
            self._transition(key, "info", "breaker_half_open")
        if b.state == "half_open":
            if b.trial:
                return False
            b.trial = True
            return True
        return b.state == "closed"

    def success(self, key: Key, trial: bool = True) -> None:
        """Record a call that worked; the half-open trial that works closes the breaker.

        `trial` is False for a call let through before the breaker opened: while half-open, only the trial's
        verdict counts.
        """
        if self._get(key).state == "half_open" and trial:
            self._breakers[key] = _Breaker()
            self._transition(key, "info", "breaker_closed")

    def failure(self, key: Key, rate_limited: bool = False, trial: bool = True) -> None:
        b = self._get(key)
        now = self.clock()
        if b.state == "half_open":
            if trial:
                self._open(key, b, now, rate_limited)
            return
        if b.state == "open":
            return
        b.failures.append(now)
        while b.failures and now - b.failures[0] > self.limits.breaker_window:
            b.failures.popleft()
        if rate_limited or len(b.failures) >= self.limits.breaker_failures:
            self._open(key, b, now, rate_limited)

    def release(self, key: Key, trial: bool = True) -> None:
        """A call let through ended without a verdict (its caller was cancelled): the next call may try."""
        b = self._get(key)
        if b.state == "half_open" and trial:
            b.trial = False

    def _open(self, key: Key, b: _Breaker, now: float, rate_limited: bool) -> None:
        b.state, b.opened_at, b.trial = "open", now, False
        b.failures.clear()
        self._transition(key, "warning", "breaker_opened", rate_limited=rate_limited)

    def _transition(self, key: Key, level: str, event: str, **extra: object) -> None:
        fields = {"provider": key[0], "model": key[1], **extra}
        getattr(log, level)(event, **fields)
        self.event_log.write(level, "breaker", event, fields)
