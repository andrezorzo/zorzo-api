"""F01-T02 AC4: hybrid search over the active version, through the Postgres adapter with handcrafted vectors;
F01-T14 AC3: full text in the domain's languages."""

from collections.abc import AsyncIterator

import psycopg
import pytest
from rag_core.adapters.pgvector_store import (
    PgCorpusStore,
    PgVectorStore,
    TextSearchConfigError,
    check_text_search,
    to_vector,
)
from rag_core.adapters.postgres import Database
from rag_core.domain import load_domain
from rag_core.ports import EMBEDDING_DIM, ChunkRow, DocumentRow

from tests.conftest import create_migrated_database, drop_database

DIM = EMBEDDING_DIM
ZORZO_TEXT_SEARCH = load_domain("zorzo").languages


def unit(*weights: tuple[int, float]) -> list[float]:
    v = [0.0] * DIM
    for i, w in weights:
        v[i] = w
    norm = sum(x * x for x in v) ** 0.5
    return [x / norm for x in v]


def add_version(
    conn: psycopg.Connection[tuple[object, ...]], chunks: list[tuple[str, list[float]]]
) -> list[int]:
    row = conn.execute(
        "INSERT INTO corpus_versions (embed_model_tag) VALUES ('t') RETURNING version"
    ).fetchone()
    assert row is not None
    version = row[0]
    doc = conn.execute(
        "INSERT INTO documents (corpus_version, doc_key, title, kind, lang, authority, hash) "
        "VALUES (%s, 'd', 'Doc', 'faq', 'en', 3, 'h') RETURNING id",
        (version,),
    ).fetchone()
    assert doc is not None
    ids = []
    for i, (content, vector) in enumerate(chunks):
        r = conn.execute(
            "INSERT INTO chunks (document_id, corpus_version, heading_path, content, tokens, hash, embedding,"
            " tsv) VALUES (%s, %s, 'Doc', %s, 1, %s, CAST(%s AS vector), to_tsvector('english', %s))"
            " RETURNING id",
            (doc[0], version, content, f"h{version}-{i}", to_vector(vector), content),
        ).fetchone()
        assert r is not None
        ids.append(int(str(r[0])))
    return [int(str(version)), *ids]


@pytest.fixture
async def store() -> AsyncIterator[tuple[PgVectorStore, str]]:
    url = create_migrated_database()
    db = Database(url)
    yield PgVectorStore(db, rrf_k=60, min_similarity=0.5, text_search=ZORZO_TEXT_SEARCH), url
    await db.close()
    drop_database(url)


async def test_searches_only_the_given_version(store: tuple[PgVectorStore, str]) -> None:
    vs, url = store
    with psycopg.connect(url) as conn:
        old = add_version(conn, [("serverless pipelines on aws", unit((0, 1)))])
        new = add_version(conn, [("serverless pipelines on aws", unit((0, 1)))])
    hits = await vs.search(unit((0, 1)), "serverless", k=20, version=new[0])
    assert [h.id for h in hits] == new[1:]
    assert old[1] not in [h.id for h in hits]


async def test_a_hit_carries_its_documents_authority(store: tuple[PgVectorStore, str]) -> None:
    vs, url = store
    with psycopg.connect(url) as conn:
        version = add_version(conn, [("serverless pipelines on aws", unit((0, 1)))])
    hits = await vs.search(unit((0, 1)), "serverless", k=20, version=version[0])
    assert [h.authority for h in hits] == [3]  # the fixture document's authority


async def test_fuses_vector_and_full_text_hits_with_rrf(store: tuple[PgVectorStore, str]) -> None:
    vs, url = store
    query = unit((0, 1))
    with psycopg.connect(url) as conn:
        version, by_meaning, by_words, by_both = add_version(
            conn,
            [
                ("alpha beta gamma", unit((0, 1))),  # same vector as the query, no word in common
                ("kubernetes", unit((5, 1))),  # orthogonal vector, matches the word
                ("kubernetes kubernetes cluster", unit((0, 1), (1, 0.2))),  # close vector and the word
            ],
        )
    hits = await vs.search(query, "kubernetes", k=20, version=version)
    ids = [h.id for h in hits]
    assert ids[0] == by_both
    assert {by_meaning, by_words} <= set(ids)
    assert hits[0].title == "Doc" and hits[0].content == "kubernetes kubernetes cluster"


async def test_ignores_vector_hits_below_the_similarity_floor(store: tuple[PgVectorStore, str]) -> None:
    vs, url = store
    with psycopg.connect(url) as conn:
        version, _unrelated = add_version(
            conn, [("alpha beta gamma", unit((0, 1), (1, 1.5)))]
        )  # cosine ≈ 0.55
        far = add_version(conn, [("alpha beta gamma", unit((0, 1), (1, 3)))])  # cosine ≈ 0.32
    assert len(await vs.search(unit((0, 1)), "zzz", k=20, version=version)) == 1
    assert await vs.search(unit((0, 1)), "zzz", k=20, version=far[0]) == []


async def test_full_text_matches_any_term_of_the_question(store: tuple[PgVectorStore, str]) -> None:
    vs, url = store
    with psycopg.connect(url) as conn:
        version, chunk = add_version(conn, [("serverless pipelines on aws", unit((5, 1)))])
    hits = await vs.search(unit((0, 1)), "Which AWS services did he use?", k=20, version=version)
    assert [h.id for h in hits] == [chunk]


async def test_full_text_uses_the_domains_configurations(store: tuple[PgVectorStore, str]) -> None:
    zorzo, _ = store
    text_search = {"en": "english", "es": "spanish"}
    corpus = PgCorpusStore(zorzo.db, text_search)
    version = await corpus.create_candidate("t")
    row = DocumentRow("guia", "Guía", "manual", "es", 1, None, None, "h")
    await corpus.write(
        version, [row], [ChunkRow("guia", "Guía", "Los árboles del parque", 1, "c", unit((5, 1)))]
    )
    other = PgVectorStore(zorzo.db, rrf_k=60, min_similarity=0.5, text_search=text_search)
    # "árbol" and "árboles" share a stem only in Spanish (`arbol`; English and Portuguese keep the accent):
    # the chunk was indexed with `spanish`, and the query is searched in it.
    assert [h.title for h in await other.search(unit((0, 1)), "árbol", k=20, version=version)] == ["Guía"]
    assert await zorzo.search(unit((0, 1)), "árbol", k=20, version=version) == []


async def test_configurations_are_checked_as_the_queries_resolve_them(
    store: tuple[PgVectorStore, str],
) -> None:
    vs, _ = store
    await check_text_search(
        vs.db, ["English", "pg_catalog.portuguese"]
    )  # what `CAST(… AS regconfig)` accepts
    # Bad names on both sides of a good one: a failed cast doesn't spoil the checks after it.
    with pytest.raises(TextSearchConfigError, match=r"\['aaa_klingon', 'zzz_klingon'\]"):
        await check_text_search(vs.db, ["zzz_klingon", "english", "aaa_klingon"])
