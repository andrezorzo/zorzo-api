"""Postgres adapter: async engine and schema reflection (SQLAlchemy Core, no ORM), and the connections of
the LangGraph checkpointer.

Django owns the schema (LLD "Data"); this module reflects it at startup and never declares a table. The
checkpointer's tables are the library's, in their own schema (LLD "4. Data", conventions).
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import psycopg
from pgvector.sqlalchemy import Vector
from psycopg import sql
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy import MetaData, make_url, text
from sqlalchemy.dialects.postgresql.base import ischema_names
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from rag_core.ports import StoreUnavailableError
from rag_core.units import MS_PER_S

CONNECT_TIMEOUT_S = 5
DEAD_SOCKET_MS = 10_000  # unacknowledged data for this long ends the connection
# An idle connection is probed after KEEPALIVE_IDLE_S, then every KEEPALIVE_INTERVAL_S; KEEPALIVE_PROBES
# unanswered probes end it.
KEEPALIVE_IDLE_S = 5
KEEPALIVE_INTERVAL_S = 2
KEEPALIVE_PROBES = 2
POOL_WAIT_S = 5
# libpq's dead-socket bounds, shared by the engine and the checkpointer's connections: a connection that stops
# answering (a half-open socket, a partition) fails in seconds instead of hanging a statement for good.
DEAD_SOCKET_ARGS = {
    "connect_timeout": CONNECT_TIMEOUT_S,
    "keepalives": 1,
    "keepalives_idle": KEEPALIVE_IDLE_S,
    "keepalives_interval": KEEPALIVE_INTERVAL_S,
    "keepalives_count": KEEPALIVE_PROBES,
    "tcp_user_timeout": DEAD_SOCKET_MS,
}
PROBE_TIMEOUT_S = 2.0
# A short statement (a budget row, an alert row, a log row) that takes seconds is stuck: Postgres ends a
# statement or lock wait past this. No client-side timeout: one could cut a COMMIT the server already did.
SHORT_STATEMENT_S = 2.0

# The LangGraph checkpointer's schema, outside Django's; its pool's size.
CHECKPOINT_SCHEMA = "langgraph"
CHECKPOINT_POOL_MIN = 1
CHECKPOINT_POOL_MAX = 10

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
            # TCP keepalives and libpq's user timeout; waiting for a pooled connection is bounded too.
            connect_args=dict(DEAD_SOCKET_ARGS),
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
                await conn.execute(BOUNDS, {"ms": str(int(SHORT_STATEMENT_S * MS_PER_S))})
                yield conn
        except (OSError, SQLAlchemyError) as exc:
            raise StoreUnavailableError(type(exc).__name__) from exc

    async def ping(self) -> None:
        async with asyncio.timeout(PROBE_TIMEOUT_S), self.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))


def _checkpoint_conninfo(url: str) -> str:
    """The libpq form of the database URL (SQLAlchemy's may name a driver)."""
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


def _checkpoint_kwargs(options: str) -> dict[str, Any]:
    """What the checkpointer needs of a connection (as its own `from_conn_string` sets: autocommit, rows as
    dicts, no prepared statements), the engine's dead-socket bounds, and the session `options`."""
    return {
        "autocommit": True,
        "row_factory": dict_row,
        "prepare_threshold": 0,
        **DEAD_SOCKET_ARGS,
        "options": options,
    }


async def open_checkpoint_pool(url: str) -> AsyncConnectionPool[psycopg.AsyncConnection[DictRow]]:
    """The request-time connections of the checkpointer, in its schema: a statement or lock wait past
    `SHORT_STATEMENT_S` is ended by Postgres, and waiting for a connection is bounded by `POOL_WAIT_S`."""
    ms = int(SHORT_STATEMENT_S * MS_PER_S)
    options = f"-c search_path={CHECKPOINT_SCHEMA} -c statement_timeout={ms} -c lock_timeout={ms}"
    pool: AsyncConnectionPool[psycopg.AsyncConnection[DictRow]] = AsyncConnectionPool(
        _checkpoint_conninfo(url),
        connection_class=psycopg.AsyncConnection[DictRow],
        kwargs=_checkpoint_kwargs(options),
        min_size=CHECKPOINT_POOL_MIN,
        max_size=CHECKPOINT_POOL_MAX,
        open=False,
        check=AsyncConnectionPool.check_connection,  # a connection that died while idle is replaced
        timeout=POOL_WAIT_S,
    )
    await pool.open(wait=True, timeout=POOL_WAIT_S)
    return pool


@asynccontextmanager
async def checkpoint_setup_connection(url: str) -> AsyncIterator[psycopg.AsyncConnection[DictRow]]:
    """One connection for the checkpointer's migrations, with its schema created when missing. No statement
    timeout: a migration (`CREATE INDEX CONCURRENTLY`) may take longer than a request's statement."""
    async with await psycopg.AsyncConnection.connect(
        _checkpoint_conninfo(url), **_checkpoint_kwargs(f"-c search_path={CHECKPOINT_SCHEMA}")
    ) as conn:
        await conn.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(CHECKPOINT_SCHEMA))
        )
        yield conn
