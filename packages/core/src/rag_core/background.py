"""Background work a caller never waits on (alerts, breaker rows, settles of cancelled calls)."""

import asyncio
from collections.abc import Coroutine
from typing import Any

import structlog

log = structlog.get_logger()

CANCEL_WAIT_S = 0.5


class BackgroundTasks:
    """Holds the running tasks (the event loop keeps only weak references) until they end."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()

    def spawn(self, work: Coroutine[Any, Any, None]) -> None:
        task = asyncio.get_running_loop().create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._done)

    def _done(self, task: "asyncio.Task[None]") -> None:
        self._tasks.discard(task)
        if not task.cancelled() and (exc := task.exception()) is not None:
            # A bug in background work: logged by type (its text may quote a query), never lost.
            log.error("background_task_failed", task=_name(task), error=type(exc).__name__)

    async def drain(self, timeout: float | None = None) -> None:
        """Wait until nothing runs, including work spawned meanwhile; at shutdown and in tests.

        With `timeout`, what still runs after it is logged and cancelled, and gets `CANCEL_WAIT_S` more to
        unwind (a rollback, a connection back to the pool): the call never takes much longer than `timeout`.
        """
        loop = asyncio.get_running_loop()
        ends_at = None if timeout is None else loop.time() + timeout
        while self._tasks:
            left = None if ends_at is None else ends_at - loop.time()
            if left is not None and left <= 0:
                break
            await asyncio.wait(set(self._tasks), timeout=left)
        if not self._tasks:
            return
        stuck = list(self._tasks)
        log.error("background_tasks_stuck", tasks=sorted(_name(t) for t in stuck))
        for task in stuck:
            task.cancel()
        await asyncio.wait(stuck, timeout=CANCEL_WAIT_S)


def _name(task: "asyncio.Task[None]") -> str:
    return getattr(task.get_coro(), "__qualname__", task.get_name())
