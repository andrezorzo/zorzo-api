"""Incident rules over `alert_states` (LLD "6.10 Alerts"): one message when an incident opens, a reminder
every `ALERT_REPEAT_AFTER` while it stays open, one when it closes. The Alerter only delivers.

Nothing here raises: a store or an alerter that fails is logged, so an alert never fails a request.
"""

import asyncio
from collections import defaultdict
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any, get_args

import structlog

from rag_core.limits import Limits
from rag_core.ports import Alerter, AlertKind, AlertStore, StoreUnavailable

log = structlog.get_logger()

SEND_TIMEOUT_S = 5.0  # a slow alerter gives up instead of piling up tasks


class Alerts:
    def __init__(
        self,
        store: AlertStore,
        alerter: Alerter,
        limits: Limits,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.alerter = alerter
        self.limits = limits
        self.clock = clock
        # Kinds that may have an open row: all of them at start (a restart doesn't know), then what this
        # process opened. `clear` skips the database for the others, so it can run on every question.
        self._maybe_open: set[AlertKind] = set(get_args(AlertKind))
        self._tasks: set[asyncio.Task[None]] = set()
        # One kind's raises and clears run in the order they were asked for (asyncio.Lock is first come,
        # first served), so a late clear can't close the incident a later raise opened.
        self._locks: dict[AlertKind, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def raise_(self, kind: AlertKind, message: str) -> None:
        self._maybe_open.add(kind)
        async with self._locks[kind]:
            now = self.clock()
            remind_before = now - timedelta(seconds=self.limits.alert_repeat_after)
            try:
                outcome = await self.store.open(kind, now, remind_before)
            except StoreUnavailable as exc:
                log.error("alert_store_failed", kind=kind, error=str(exc))
                return
            if outcome is not None:
                await self._send(kind, message if outcome == "opened" else f"still open: {message}")

    async def clear(self, kind: AlertKind) -> None:
        if kind in self._maybe_open:
            self._maybe_open.discard(kind)
            await self._clear(kind)

    def raise_soon(self, kind: AlertKind, message: str) -> None:
        """`raise_` in the background: the caller never waits on the store or the alerter."""
        self._spawn(self.raise_(kind, message))

    def clear_soon(self, kind: AlertKind) -> None:
        if kind in self._maybe_open:
            self._maybe_open.discard(kind)
            self._spawn(self._clear(kind))

    async def _clear(self, kind: AlertKind) -> None:
        async with self._locks[kind]:
            try:
                closed = await self.store.close(kind, self.clock())
            except StoreUnavailable as exc:
                self._maybe_open.add(kind)  # try again on the next check
                log.error("alert_store_failed", kind=kind, error=str(exc))
                return
            if closed:
                await self._send(kind, f"resolved: {kind}")

    async def drain(self) -> None:
        """Wait for the background work, at shutdown and in tests."""
        while self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    def _spawn(self, work: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _send(self, kind: AlertKind, message: str) -> None:
        try:
            async with asyncio.timeout(SEND_TIMEOUT_S):
                await self.alerter.send(kind, message)
        except Exception as exc:
            # The exception's text may quote the request, whose URL holds the bot token.
            log.warning("alert_send_failed", kind=kind, error=type(exc).__name__)
        else:
            log.info("alert_sent", kind=kind)
