"""Incident rules over `alert_states` (LLD "6.10 Alerts"): one message when an incident opens, a reminder
every `ALERT_REPEAT_AFTER` while it stays open, one when it closes. The Alerter only delivers.

Nothing here raises: a store or an alerter that fails is logged, so an alert never fails a request.
"""

import asyncio
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import get_args

import structlog

from rag_core.background import BackgroundTasks
from rag_core.limits import Limits
from rag_core.ports import Alerter, AlertKind, AlertStore, StoreUnavailableError

log = structlog.get_logger()

SEND_TIMEOUT_S = 5.0  # a slow alerter gives up instead of piling up tasks
# `raise_soon` runs on every question: while an incident lasts, the store is asked at most this often per
# kind. A reminder can then be late by up to this much.
RAISE_CHECK_EVERY_S = 60.0


class Alerts:
    def __init__(
        self,
        store: AlertStore,
        alerter: Alerter,
        limits: Limits,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self.alerter = alerter
        self.limits = limits
        self.clock = clock
        # Kinds that may have an open row: all of them at start (a restart doesn't know), then what this
        # process opened. `clear` skips the database for the others, so it can run on every question.
        self._maybe_open: set[AlertKind] = set(get_args(AlertKind))
        self.monotonic = monotonic
        self._background = BackgroundTasks()
        self._raise_checked: dict[AlertKind, float] = {}  # when `raise_soon` last asked the store, per kind
        # The severity last told per kind while its incident is open, written only when a message went out and
        # removed only when the incident closed, both under the kind's lock. A higher one is a worse state,
        # told without waiting for the reminder. A restart forgets it (0), so a restart during a severe state
        # tells it once more.
        self._told: dict[AlertKind, int] = {}
        # One kind's raises and clears run in the order they were asked for (asyncio.Lock is first come,
        # first served), so a late clear can't close the incident a later raise opened.
        self._locks: dict[AlertKind, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def raise_(self, kind: AlertKind, message: str, severity: int = 0) -> None:
        """Open the incident; remind after `ALERT_REPEAT_AFTER`, or at once when `severity` is worse."""
        self._maybe_open.add(kind)
        async with self._locks[kind]:
            now = self.clock()
            worse = severity > self._told.get(kind, 0)
            remind_before = now if worse else now - timedelta(seconds=self.limits.alert_repeat_after)
            try:
                outcome = await self.store.open(kind, now, remind_before)
            except StoreUnavailableError as exc:  # `raise_soon` asks again after its interval
                log.error("alert_store_failed", kind=kind, error=str(exc))
                return
            if outcome is not None:
                self._told[kind] = severity
                # A reminder carries the current message, so an incident that got worse says so.
                await self._send(kind, message if outcome == "opened" else f"still open: {message}")

    async def clear(self, kind: AlertKind) -> None:
        if self._claim_clear(kind):
            await self._clear(kind)

    def raise_soon(self, kind: AlertKind, message: str, severity: int = 0) -> None:
        """`raise_` in the background, at most once per `RAISE_CHECK_EVERY_S` per kind: the caller never waits
        on the store or the alerter, and a long incident (or a failing store) is asked once a minute."""
        now = self.monotonic()
        last = self._raise_checked.get(kind)
        if last is not None and now - last < RAISE_CHECK_EVERY_S:
            return
        self._raise_checked[kind] = now
        self._background.spawn(self.raise_(kind, message, severity))

    def clear_soon(self, kind: AlertKind) -> None:
        if self._claim_clear(kind):
            self._background.spawn(self._clear(kind))

    def _claim_clear(self, kind: AlertKind) -> bool:
        """Whether a clear must ask the store; a new incident is then raised at once."""
        if kind not in self._maybe_open:
            return False
        self._maybe_open.discard(kind)
        self._raise_checked.pop(kind, None)
        return True

    async def _clear(self, kind: AlertKind) -> None:
        async with self._locks[kind]:
            try:
                closed = await self.store.close(kind, self.clock())
            except StoreUnavailableError as exc:
                self._maybe_open.add(kind)  # try again on the next check
                log.error("alert_store_failed", kind=kind, error=str(exc))
                return
            self._told.pop(kind, None)
            if closed:
                await self._send(kind, f"resolved: {kind}")

    async def drain(self, timeout: float | None = None) -> None:
        """Wait for the background work, at shutdown and in tests."""
        await self._background.drain(timeout)

    async def _send(self, kind: AlertKind, message: str) -> None:
        try:
            async with asyncio.timeout(SEND_TIMEOUT_S):
                await self.alerter.send(kind, message)
        except Exception as exc:
            # The exception's text may quote the request, whose URL holds the bot token.
            log.warning("alert_send_failed", kind=kind, error=type(exc).__name__)
        else:
            log.info("alert_sent", kind=kind)
