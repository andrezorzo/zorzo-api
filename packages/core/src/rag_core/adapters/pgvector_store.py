"""Corpus tables in Postgres (ADR 0001, ADR 0013): the hybrid search and the ingest writes.

Vectors travel as pgvector's text form (`[1,2,3]`) with explicit casts, so no driver adapter is needed.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from pgvector import Vector
from sqlalchemy import text

from rag_core.adapters.postgres import Database
from rag_core.ports import ActiveCorpus, ChunkRow, DocumentRow, PublishedChunk, SearchHit

log = structlog.get_logger()

INGEST_LOCK_KEY = 0x7A6F727A6F  # one ingest run at a time (LLD "Concurrency and idempotency")
TEXT_SEARCH_CONFIG = {"en": "english", "pt": "portuguese"}

HYBRID_SEARCH = text("""
WITH vec AS (
    SELECT id, row_number() OVER (ORDER BY embedding <=> CAST(:vector AS vector)) AS rank
    FROM chunks
    -- Nearest neighbours always exist; below the floor they aren't a match (LLD "Values and configuration").
    WHERE corpus_version = :version AND 1 - (embedding <=> CAST(:vector AS vector)) >= :min_similarity
    ORDER BY embedding <=> CAST(:vector AS vector)
    LIMIT :k
), query AS (
    -- Any term of the question matches (OR), ranked by how many and how close; the question's language
    -- isn't known here, so both configurations are searched.
    SELECT replace(plainto_tsquery('english', :text)::text, '&', '|')::tsquery
        || replace(plainto_tsquery('portuguese', :text)::text, '&', '|')::tsquery AS q
), fts AS (
    SELECT c.id, row_number() OVER (ORDER BY ts_rank_cd(c.tsv, query.q) DESC, c.id) AS rank
    FROM chunks c, query
    WHERE c.corpus_version = :version AND c.tsv @@ query.q
    ORDER BY ts_rank_cd(c.tsv, query.q) DESC, c.id
    LIMIT :k
), fused AS (
    SELECT id, sum(1.0 / (:rrf_k + rank)) AS score
    FROM (SELECT * FROM vec UNION ALL SELECT * FROM fts) ranked
    GROUP BY id
)
SELECT c.id, d.title, c.content, f.score
FROM fused f
JOIN chunks c ON c.id = f.id
JOIN documents d ON d.id = c.document_id
ORDER BY f.score DESC, c.id
""")


def to_vector(values: list[float]) -> str:
    return str(Vector(values).to_text())


def from_vector(text_value: str) -> list[float]:
    return [float(v) for v in Vector.from_text(text_value).to_list()]


class PgVectorStore:
    """Read side, used by the API."""

    def __init__(self, db: Database, rrf_k: int, min_similarity: float) -> None:
        self.db = db
        self.rrf_k = rrf_k
        self.min_similarity = min_similarity

    async def active_corpus(self) -> ActiveCorpus | None:
        async with self.db.engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT version, embed_model_tag FROM corpus_versions WHERE status = 'active'")
                )
            ).first()
        return ActiveCorpus(row.version, row.embed_model_tag) if row else None

    async def search(self, vector: list[float], text_query: str, k: int, version: int) -> list[SearchHit]:
        async with self.db.engine.begin() as conn:
            # Keep scanning the HNSW graph until enough rows pass the version filter (pgvector 0.8).
            await conn.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
            rows = await conn.execute(
                HYBRID_SEARCH,
                {
                    "vector": to_vector(vector),
                    "text": text_query,
                    "k": k,
                    "version": version,
                    "rrf_k": self.rrf_k,
                    "min_similarity": self.min_similarity,
                },
            )
            return [SearchHit(r.id, r.title, r.content, float(r.score)) for r in rows]


class PgCorpusStore:
    """Write side, used by ingest (LLD "6.1 Corpus version")."""

    def __init__(self, db: Database) -> None:
        self.db = db

    @asynccontextmanager
    async def lock(self) -> AsyncIterator[bool]:
        # Autocommit: the session lock must not hold a transaction open for the whole run, where an idle
        # timeout could end it and free the lock mid-run.
        async with self.db.engine.connect() as base:
            conn = await base.execution_options(isolation_level="AUTOCOMMIT")
            got = bool(await conn.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": INGEST_LOCK_KEY}))
            try:
                yield got
            finally:
                if got:
                    try:
                        await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": INGEST_LOCK_KEY})
                    except Exception:
                        # Closing the connection frees the lock anyway; never hide why the run stopped.
                        log.warning("ingest_unlock_failed", exc_info=True)

    async def create_candidate(self, embed_model_tag: str) -> int:
        async with self.db.engine.begin() as conn:
            version = await conn.scalar(
                text("INSERT INTO corpus_versions (embed_model_tag) VALUES (:tag) RETURNING version"),
                {"tag": embed_model_tag},
            )
        return int(version)

    async def reject(self, version: int) -> None:
        async with self.db.engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE corpus_versions SET status = 'rejected'"
                    " WHERE version = :v AND status = 'candidate'"
                ),
                {"v": version},
            )

    async def reusable_embeddings(self, hashes: list[str], embed_model_tag: str) -> dict[str, list[float]]:
        if not hashes:
            return {}
        async with self.db.engine.connect() as conn:
            rows = await conn.execute(
                text("""
                    SELECT DISTINCT ON (c.hash) c.hash, c.embedding::text AS embedding
                    FROM chunks c JOIN corpus_versions v ON v.version = c.corpus_version
                    WHERE v.embed_model_tag = :tag AND c.hash = ANY(:hashes)
                    ORDER BY c.hash, c.corpus_version DESC
                """),
                {"tag": embed_model_tag, "hashes": hashes},
            )
            return {r.hash: from_vector(r.embedding) for r in rows}

    async def write(self, version: int, documents: list[DocumentRow], chunks: list[ChunkRow]) -> None:
        async with self.db.engine.begin() as conn:
            doc_ids: dict[str, tuple[int, str]] = {}
            for d in documents:
                doc_id = await conn.scalar(
                    text("""
                        INSERT INTO documents (corpus_version, doc_key, title, kind, lang, authority,
                                               source_url, updated, hash)
                        VALUES (:v, :doc_key, :title, :kind, :lang, :authority, :source_url,
                                CAST(:updated AS date), :hash)
                        RETURNING id
                    """),
                    {"v": version, **d.__dict__},
                )
                doc_ids[d.doc_key] = (int(doc_id), d.lang)
            if chunks:
                await conn.execute(
                    text("""
                        INSERT INTO chunks (document_id, corpus_version, heading_path, content, tokens, hash,
                                            embedding, tsv)
                        VALUES (:document_id, :v, :heading_path, :content, :tokens, :hash,
                                CAST(:embedding AS vector),
                                to_tsvector(CAST(:config AS regconfig), :heading_path || ' ' || :content))
                        ON CONFLICT (corpus_version, hash) DO NOTHING
                    """),
                    [
                        {
                            "document_id": doc_ids[c.doc_key][0],
                            "v": version,
                            "heading_path": c.heading_path,
                            "content": c.content,
                            "tokens": c.tokens,
                            "hash": c.hash,
                            "embedding": to_vector(c.embedding),
                            "config": TEXT_SEARCH_CONFIG[doc_ids[c.doc_key][1]],
                        }
                        for c in chunks
                    ],
                )

    async def embed_model_tag(self, version: int) -> str | None:
        async with self.db.engine.connect() as conn:
            tag = await conn.scalar(
                text("SELECT embed_model_tag FROM corpus_versions WHERE version = :v"), {"v": version}
            )
        return None if tag is None else str(tag)

    async def eval_passed(self, version: int) -> bool:
        async with self.db.engine.connect() as conn:
            passed = await conn.scalar(
                text("""
                    SELECT coalesce(r.passed, false) FROM corpus_versions v
                    LEFT JOIN eval_runs r ON r.id = v.eval_run_id WHERE v.version = :v
                """),
                {"v": version},
            )
        return bool(passed)

    async def activate(self, version: int) -> None:
        async with self.db.engine.begin() as conn:
            await conn.execute(text("UPDATE corpus_versions SET status = 'retired' WHERE status = 'active'"))
            result = await conn.execute(
                text("""
                    UPDATE corpus_versions SET status = 'active', activated_at = now()
                    WHERE version = :v AND status = 'candidate'
                      AND EXISTS (SELECT 1 FROM documents WHERE corpus_version = :v)
                """),
                {"v": version},
            )
            if result.rowcount != 1:
                # Rolls back the retirement above: an empty or unknown version never replaces the active one.
                raise LookupError(f"corpus version {version} is not a candidate with documents")

    async def published_chunks(self, version: int) -> list[PublishedChunk]:
        async with self.db.engine.connect() as conn:
            rows = await conn.execute(
                text("""
                    SELECT c.id, d.doc_key, d.title, d.kind, d.lang, c.heading_path, c.content, d.source_url,
                           c.embedding::text AS embedding
                    FROM chunks c JOIN documents d ON d.id = c.document_id
                    WHERE c.corpus_version = :v ORDER BY c.id
                """),
                {"v": version},
            )
            return [
                PublishedChunk(
                    r.id,
                    r.doc_key,
                    r.title,
                    r.kind,
                    r.lang,
                    r.heading_path,
                    r.content,
                    r.source_url,
                    from_vector(r.embedding),
                )
                for r in rows
            ]
