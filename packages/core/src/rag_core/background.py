"""Background work a caller never waits on (alerts, breaker rows, settles of cancelled calls)."""

import asyncio
from collections.abc import Coroutine
from typing import Any

import structlog

log = structlog.get_logger()

CANCEL_WAIT_S = 1.0


class BackgroundTasks:
    """Holds the running tasks (the event loop keeps only weak references) until they end."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()

    def spawn(self, work: Coroutine[Any, Any, None]) -> None:
        task = asyncio.get_running_loop().create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self, timeout: float | None = None) -> None:
        """Wait until nothing runs, including work spawned meanwhile; at shutdown and in tests.

        With `timeout`, what still runs after it is logged and cancelled, so a stuck task can't hold up the
        shutdown.
        """
        try:
            async with asyncio.timeout(timeout):
                while self._tasks:
                    await asyncio.gather(*self._tasks, return_exceptions=True)
        except TimeoutError:
            stuck = list(self._tasks)
            log.error("background_tasks_stuck", tasks=sorted(t.get_coro().__qualname__ for t in stuck))  # type: ignore[union-attr]
            for task in stuck:
                task.cancel()
            # Let them unwind (a rollback, a connection back to the pool) before the caller closes what they
            # use; a task that ignores the cancel is left behind after this.
            await asyncio.wait(stuck, timeout=CANCEL_WAIT_S)
