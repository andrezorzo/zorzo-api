"""F01-T09 AC1 (integration): the Postgres answer cache's lookup and write against a real database."""

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import psycopg
import pytest
from rag_core.adapters.answer_cache import PgAnswerCache
from rag_core.adapters.fake_embedder import HashEmbedder
from rag_core.adapters.postgres import Database
from rag_core.ports import CacheEntry

from tests.conftest import reset_tables

CLAIMS: list[dict[str, Any]] = [{"i": 0, "text": "He used EC2.", "sources": [1], "verdict": "supported"}]
SIMILARITY = 0.95
TTL_S = 3600.0
VERSION = "cfg-1"
QUESTION = "Which AWS services has he used"


@pytest.fixture
async def cache(corpus_database: str) -> AsyncIterator[PgAnswerCache]:
    reset_tables(corpus_database, "answer_cache")
    db = Database(corpus_database)
    yield PgAnswerCache(db)
    await db.close()
    reset_tables(corpus_database, "answer_cache")


def active_version(url: str) -> int:
    with psycopg.connect(url) as conn:
        row = conn.execute("SELECT version FROM corpus_versions WHERE status = 'active'").fetchone()
    assert row is not None
    return int(row[0])


async def vector_of(text: str) -> list[float]:
    return (await HashEmbedder().embed([text]))[0]


async def entry(url: str, question: str = QUESTION, **changes: Any) -> CacheEntry:
    values: dict[str, Any] = {
        "question": question,
        "lang": "en",
        "corpus_version": active_version(url),
        "model_config_version": VERSION,
        "claims": CLAIMS,
        "vector": await vector_of(question),
    }
    return CacheEntry(**{**values, **changes})


async def lookup(cache: PgAnswerCache, url: str, question: str = QUESTION, **changes: Any) -> Any:
    values: dict[str, Any] = {
        "lang": "en",
        "corpus_version": active_version(url),
        "model_config_version": VERSION,
    }
    values.update(changes)
    return await cache.lookup(
        await vector_of(question),
        values["lang"],
        values["corpus_version"],
        values["model_config_version"],
        SIMILARITY,
    )


def hits(url: str) -> list[int]:
    with psycopg.connect(url) as conn:
        return [r[0] for r in conn.execute("SELECT hits FROM answer_cache ORDER BY id").fetchall()]


async def test_a_hit_needs_the_same_language_and_versions(cache: PgAnswerCache, corpus_database: str) -> None:
    await cache.store(await entry(corpus_database), TTL_S)
    found = await lookup(cache, corpus_database)
    assert found is not None and found.claims == CLAIMS and found.similarity >= SIMILARITY
    # Each of the three differs alone from the stored row, and none matches.
    assert await lookup(cache, corpus_database, lang="pt") is None
    assert await lookup(cache, corpus_database, model_config_version="cfg-2") is None
    assert await lookup(cache, corpus_database, corpus_version=active_version(corpus_database) + 1) is None


async def test_a_similar_enough_question_hits_and_a_farther_one_misses(
    cache: PgAnswerCache, corpus_database: str
) -> None:
    await cache.store(await entry(corpus_database), TTL_S)
    assert await lookup(cache, corpus_database, "which AWS services has he used?") is not None
    assert await lookup(cache, corpus_database, QUESTION + " in production") is None


async def test_an_expired_entry_never_matches(cache: PgAnswerCache, corpus_database: str) -> None:
    await cache.store(await entry(corpus_database), TTL_S)
    with psycopg.connect(corpus_database) as conn:
        conn.execute("UPDATE answer_cache SET expires_at = now() - interval '1 second'")
    assert await lookup(cache, corpus_database) is None
    assert hits(corpus_database) == [0]


async def test_the_nearest_entry_wins_and_counts_the_hit(cache: PgAnswerCache, corpus_database: str) -> None:
    nearer = "Which AWS services has he used"
    farther = "Which AWS services has he used before"  # similar enough to match too
    await cache.store(
        await entry(corpus_database, farther, claims=[{**CLAIMS[0], "text": "Farther."}]), TTL_S
    )
    await cache.store(await entry(corpus_database, nearer, claims=[{**CLAIMS[0], "text": "Nearer."}]), TTL_S)
    found = await cache.lookup(await vector_of(nearer), "en", active_version(corpus_database), VERSION, 0.5)
    assert found is not None and found.claims[0]["text"] == "Nearer."
    assert hits(corpus_database) == [0, 1]  # only the nearest counts it


async def test_a_stored_row_has_what_the_writer_sets(cache: PgAnswerCache, corpus_database: str) -> None:
    await cache.store(await entry(corpus_database), TTL_S)
    with psycopg.connect(corpus_database) as conn:
        row = conn.execute(
            "SELECT question_scrubbed, lang, model_config_version, answer, expires_at - created_at, "
            "trace_id, hits FROM answer_cache"
        ).fetchone()
    assert row is not None
    assert row[:4] == (QUESTION, "en", VERSION, {"claims": CLAIMS})
    assert row[4] == timedelta(seconds=TTL_S) and row[5] is None and row[6] == 0
