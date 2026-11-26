"""Background work: a drain with a timeout returns close to it, logging and cancelling what is stuck."""

import asyncio
import time

from rag_core.background import CANCEL_WAIT_S, BackgroundTasks


async def test_drain_waits_for_the_work_spawned_meanwhile() -> None:
    done: list[str] = []
    tasks = BackgroundTasks()

    async def second() -> None:
        done.append("second")

    async def first() -> None:
        await asyncio.sleep(0.01)
        tasks.spawn(second())
        done.append("first")

    tasks.spawn(first())
    await tasks.drain()
    assert done == ["first", "second"]


async def test_a_stuck_task_is_cancelled_and_the_drain_returns_in_time() -> None:
    tasks = BackgroundTasks()

    async def stuck() -> None:
        try:
            await asyncio.sleep(100)
        except asyncio.CancelledError:
            await asyncio.sleep(3)  # unwinds slowly, past the cancel wait
            raise

    tasks.spawn(stuck())
    started = time.monotonic()
    await tasks.drain(0.1)  # no exception
    assert time.monotonic() - started < 0.1 + CANCEL_WAIT_S + 0.2
