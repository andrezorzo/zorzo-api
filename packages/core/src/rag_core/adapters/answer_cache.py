"""The answer cache in Postgres (ADR 0014, LLD "6.9 Answer cache"): one lookup statement, one insert."""

import json
from typing import Any

from sqlalchemy import text

from rag_core.adapters.pgvector_store import to_vector
from rag_core.adapters.postgres import Database
from rag_core.ports import CachedAnswer, CacheEntry

# The nearest row under the filters, and only when it is close enough, in one statement: the `UPDATE` that
# counts the hit is the lookup's result.
LOOKUP = text("""
WITH nearest AS (
    SELECT id, 1 - (embedding <=> CAST(:vector AS vector)) AS similarity
    FROM answer_cache
    WHERE lang = :lang AND corpus_version = :corpus_version
        AND model_config_version = :model_config_version AND expires_at > now()
    ORDER BY embedding <=> CAST(:vector AS vector)
    LIMIT 1
)
UPDATE answer_cache SET hits = hits + 1
FROM nearest
WHERE answer_cache.id = nearest.id AND nearest.similarity >= :min_similarity
RETURNING answer_cache.answer, nearest.similarity
""")

# `statement_timestamp()`, the clock of `created_at`'s default, so `expires_at` is exactly the TTL after it.
INSERT = text("""
INSERT INTO answer_cache (question_scrubbed, lang, corpus_version, model_config_version, answer, embedding,
                          expires_at)
VALUES (:question, :lang, :corpus_version, :model_config_version, CAST(:answer AS jsonb),
        CAST(:vector AS vector), statement_timestamp() + make_interval(secs => :ttl_s))
""")


class PgAnswerCache:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def lookup(
        self,
        vector: list[float],
        lang: str,
        corpus_version: int,
        model_config_version: str,
        min_similarity: float,
    ) -> CachedAnswer | None:
        async with self.db.short_transaction() as conn:
            # The corpus search may take a farther row first (`relaxed_order`); with `LIMIT 1` that row
            # would be the one served, so the cache scans in order.
            await conn.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
            row = (
                await conn.execute(
                    LOOKUP,
                    {
                        "vector": to_vector(vector),
                        "lang": lang,
                        "corpus_version": corpus_version,
                        "model_config_version": model_config_version,
                        "min_similarity": min_similarity,
                    },
                )
            ).first()
        if row is None:
            return None
        return CachedAnswer(claims=_claims(row.answer), similarity=float(row.similarity))

    async def store(self, entry: CacheEntry, ttl_s: float) -> None:
        async with self.db.short_transaction() as conn:
            await conn.execute(
                INSERT,
                {
                    "question": entry.question,
                    "lang": entry.lang,
                    "corpus_version": entry.corpus_version,
                    "model_config_version": entry.model_config_version,
                    "answer": json.dumps({"claims": entry.claims}, ensure_ascii=False),
                    "vector": to_vector(entry.vector),
                    "ttl_s": ttl_s,
                },
            )


def _claims(answer: Any) -> list[dict[str, Any]]:
    """The stored claims; a row that doesn't hold a list of them reads as none (the service treats it as a
    miss)."""
    claims = answer.get("claims") if isinstance(answer, dict) else None
    return claims if isinstance(claims, list) else []
