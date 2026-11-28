"""The graph's memory (LLD "6.3 Agent run", conversation): LangGraph's Postgres checkpointer, its threads
keyed by the session id, storing only the conversation.

The serializer is limited to plain types: LangGraph's default rebuilds any importable class a stored value
names, and the conversation is plain dicts, lists, strings and a float.
"""

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver, Conn
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


def checkpointer(conn: Conn) -> AsyncPostgresSaver:
    """The saver over the adapter's pool (or one connection); needs a running event loop."""
    return AsyncPostgresSaver(conn, serde=JsonPlusSerializer(allowed_msgpack_modules=None))


async def setup(conn: Conn) -> None:
    """The library's own versioned migrations; idempotent."""
    await checkpointer(conn).setup()
