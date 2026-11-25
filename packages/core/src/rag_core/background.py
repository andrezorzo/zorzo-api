"""Background work a caller never waits on (alerts, breaker rows, settles of cancelled calls)."""

import asyncio
from collections.abc import Coroutine
from typing import Any


class BackgroundTasks:
    """Holds the running tasks (the event loop keeps only weak references) until they end."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()

    def spawn(self, work: Coroutine[Any, Any, None]) -> None:
        task = asyncio.get_running_loop().create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        """Wait until nothing runs, including work spawned meanwhile; at shutdown and in tests."""
        while self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
