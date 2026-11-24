"""Postgres adapter: async engine and schema reflection (SQLAlchemy Core, no ORM).

Django owns the schema (LLD "Data"); this module reflects it at startup and never declares a table.
"""

import asyncio

from sqlalchemy import MetaData, func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

PROBE_TIMEOUT_S = 2.0


def _async_url(url: str) -> str:
    scheme, sep, rest = url.partition("://")
    if scheme in ("postgres", "postgresql"):
        return f"postgresql+psycopg{sep}{rest}"
    return url


class Database:
    def __init__(self, url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(
            _async_url(url), pool_pre_ping=True, connect_args={"connect_timeout": int(PROBE_TIMEOUT_S)}
        )
        self.metadata = MetaData()

    async def reflect(self) -> None:
        async with self.engine.connect() as conn:
            await conn.run_sync(self.metadata.reflect)

    async def close(self) -> None:
        await self.engine.dispose()

    async def ping(self) -> None:
        async with asyncio.timeout(PROBE_TIMEOUT_S), self.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def degraded_today(self) -> bool:
        budget = self.metadata.tables["budget_daily"]
        today = func.timezone("UTC", func.now()).cast(budget.c.day.type)
        query = select(budget.c.degraded_at.isnot(None)).where(budget.c.day == today)
        async with asyncio.timeout(PROBE_TIMEOUT_S), self.engine.connect() as conn:
            return bool((await conn.execute(query)).scalar())
