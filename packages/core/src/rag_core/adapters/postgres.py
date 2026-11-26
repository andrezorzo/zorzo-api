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
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from rag_core.ports import StoreUnavailableError

CONNECT_TIMEOUT_S = 5
DEAD_SOCKET_MS = 10_000  # unacknowledged data for this long ends the connection
POOL_WAIT_S = 5
PROBE_TIMEOUT_S = 2.0
# A short statement (a budget row, an alert row, a log row) that takes seconds is stuck: Postgres ends a
# statement or lock wait past this. No client-side timeout: one could cut a COMMIT the server already did.
SHORT_STATEMENT_S = 2.0

BOUNDS = text("SELECT set_config('statement_timeout', :ms, true), set_config('lock_timeout', :ms, true)")


@asynccontextmanager
async def store_errors() -> AsyncIterator[None]:
    """A failing database becomes `StoreUnavailableError`, named by the error type (its text may quote
    SQL)."""
    try:
        yield
    except (OSError, SQLAlchemyError) as exc:
        raise StoreUnavailableError(type(exc).__name__) from exc


# Reflection must know pgvector's `vector` type to build the chunk and cache tables.
ischema_names["vector"] = Vector


class Database:
    def __init__(self, url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(
            make_url(url).set(drivername="postgresql+psycopg"),
            pool_pre_ping=True,
            # Errors would otherwise quote bound values, such as the visitor's question, into the logs.
            hide_parameters=True,
            # A connection that stops answering (a half-open socket, a partition) fails in seconds instead of
            # hanging a statement for good: TCP keepalives and libpq's user timeout. Waiting for a pooled
            # connection is bounded too.
            connect_args={
                "connect_timeout": CONNECT_TIMEOUT_S,
                "keepalives": 1,
                "keepalives_idle": 5,
                "keepalives_interval": 2,
                "keepalives_count": 2,
                "tcp_user_timeout": DEAD_SOCKET_MS,
            },
            pool_timeout=POOL_WAIT_S,
        )
        self.metadata = MetaData()

    async def reflect(self) -> None:
        async with self.engine.connect() as conn:
            await conn.run_sync(self.metadata.reflect)

    async def close(self) -> None:
        await self.engine.dispose()

    @asynccontextmanager
    async def short_transaction(self) -> AsyncIterator[AsyncConnection]:
        """A transaction whose statements Postgres ends past `SHORT_STATEMENT_S`; a failure (such a timeout
        included) is `StoreUnavailableError`."""
        try:
            async with self.engine.begin() as conn:
                await conn.execute(BOUNDS, {"ms": str(int(SHORT_STATEMENT_S * 1000))})
                yield conn
        except (OSError, SQLAlchemyError) as exc:
            raise StoreUnavailableError(type(exc).__name__) from exc

    async def ping(self) -> None:
        async with asyncio.timeout(PROBE_TIMEOUT_S), self.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
