"""Postgres adapter: async engine and schema reflection (SQLAlchemy Core, no ORM).

Django owns the schema (LLD "Data"); this module reflects it at startup and never declares a table.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pgvector.sqlalchemy import Vector
from sqlalchemy import MetaData, make_url, text
from sqlalchemy.dialects.postgresql.base import ischema_names
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from rag_core.ports import StoreUnavailable

CONNECT_TIMEOUT_S = 5
PROBE_TIMEOUT_S = 2.0


@asynccontextmanager
async def store_errors() -> AsyncIterator[None]:
    """A failing database becomes `StoreUnavailable`, named by the error type (its text may quote SQL)."""
    try:
        yield
    except (OSError, SQLAlchemyError) as exc:
        raise StoreUnavailable(type(exc).__name__) from exc


# Reflection must know pgvector's `vector` type to build the chunk and cache tables.
ischema_names["vector"] = Vector


class Database:
    def __init__(self, url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(
            make_url(url).set(drivername="postgresql+psycopg"),
            pool_pre_ping=True,
            # Errors would otherwise quote bound values, such as the visitor's question, into the logs.
            hide_parameters=True,
            connect_args={"connect_timeout": CONNECT_TIMEOUT_S},
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
