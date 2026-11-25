"""AC2: the circuit breaker of one chain entry (LLD "6.6 Circuit breaker")."""

from pathlib import Path
from typing import Any

from rag_core.limits import load_limits
from rag_core.services.breaker import Breakers

LIMITS = load_limits(Path(__file__).resolve().parents[2] / "config/limits.yaml").model_copy(
    update={"breaker_failures": 3, "breaker_window": 60, "breaker_cooldown": 30}
)
KEY = ("openrouter", "a/model:free")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class EventLog:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, dict[str, Any]]] = []

    def write(self, level: str, component: str, event: str, fields: dict[str, Any]) -> None:
        self.rows.append((level, component, event, fields))

    async def drain(self) -> None:
        pass


def breakers() -> tuple[Breakers, Clock, EventLog]:
    clock, log = Clock(), EventLog()
    return Breakers(LIMITS, log, clock), clock, log


def test_opens_after_repeated_failures() -> None:
    b, clock, log = breakers()
    b.failure(KEY)
    clock.now += 61  # the first failure leaves the window
    b.failure(KEY)
    b.failure(KEY)
    assert b.allow(KEY)
    b.failure(KEY)  # the third within the window
    assert not b.allow(KEY)
    assert not b.callable(KEY)
    assert log.rows == [
        ("warning", "breaker", "breaker_opened", {"provider": KEY[0], "model": KEY[1], "rate_limited": False})
    ]


def test_opens_on_one_429() -> None:
    b, _, log = breakers()
    b.failure(KEY, rate_limited=True)
    assert not b.allow(KEY)
    assert log.rows[0][2:] == ("breaker_opened", {"provider": KEY[0], "model": KEY[1], "rate_limited": True})


def test_half_open_trial_closes_or_reopens() -> None:
    b, clock, log = breakers()
    b.failure(KEY, rate_limited=True)
    clock.now += 31
    assert b.callable(KEY)
    assert b.allow(KEY)  # the one trial call
    assert not b.allow(KEY)  # the others skip the entry until it ends
    b.failure(KEY)
    assert not b.allow(KEY)  # reopened, a new cooldown
    clock.now += 31
    assert b.allow(KEY)
    b.success(KEY)  # the trial closes it
    assert b.closed(KEY)
    assert b.allow(KEY) and b.allow(KEY)
    b.success(KEY)  # already closed: no transition
    assert [row[2] for row in log.rows] == [
        "breaker_opened",
        "breaker_half_open",
        "breaker_opened",
        "breaker_half_open",
        "breaker_closed",
    ]


def test_an_abandoned_trial_lets_the_next_call_try() -> None:
    b, clock, _ = breakers()
    b.failure(KEY, rate_limited=True)
    clock.now += 31
    assert b.allow(KEY)
    b.release(KEY)  # the caller was cancelled: no verdict
    assert b.allow(KEY)


def test_entries_are_independent() -> None:
    b, _, _ = breakers()
    b.failure(KEY, rate_limited=True)
    assert b.allow(("openrouter", "other/model:free"))


def test_a_call_from_before_the_opening_doesnt_decide_the_trial() -> None:
    b, clock, _ = breakers()
    b.failure(KEY, rate_limited=True)
    clock.now += 31
    assert b.allow(KEY)  # the trial
    b.success(KEY, trial=False)  # a slow call let through while closed
    b.failure(KEY, trial=False)
    assert not b.closed(KEY) and not b.allow(KEY)  # still waiting on the trial
    b.success(KEY)
    assert b.closed(KEY)
