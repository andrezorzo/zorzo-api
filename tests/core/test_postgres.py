"""LLD "Logs": a database error never carries the visitor's question (or any bound value) into the logs."""

import pytest
from rag_core.adapters.postgres import Database
from sqlalchemy import text


async def test_database_errors_hide_bound_parameters(database_url: str) -> None:
    db = Database(database_url)
    try:
        with pytest.raises(Exception) as caught:
            async with db.engine.connect() as conn:
                # The question is bound but plays no part in the failure, as in a real search error.
                await conn.execute(text("SELECT 1 / 0 WHERE :q <> ''"), {"q": "a visitor's private question"})
        assert "private question" not in str(caught.value)
    finally:
        await db.close()
