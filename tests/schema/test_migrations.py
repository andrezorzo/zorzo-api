"""AC3: the Django migrations create every F01 table, and the API reflects them."""

import psycopg
import pytest

# Independent source of truth: the contract in the F01-T01 plan.
EXPECTED_COLUMNS: dict[str, set[str]] = {
    "corpus_versions": {"version", "status", "embed_model_tag", "created_at", "activated_at", "eval_run_id"},
    "documents": {
        "id",
        "corpus_version",
        "doc_key",
        "title",
        "kind",
        "lang",
        "authority",
        "source_url",
        "updated",
        "hash",
    },
    "chunks": {"id", "document_id", "corpus_version", "heading_path", "content", "tokens", "hash"},
    "answer_cache": {
        "id",
        "question_scrubbed",
        "lang",
        "corpus_version",
        "model_config_version",
        "answer",
        "trace_id",
        "created_at",
        "expires_at",
        "hits",
    },
    "public_traces": {
        "id",
        "created_at",
        "expires_at",
        "outcome",
        "intent",
        "lang",
        "corpus_version",
        "model_config_version",
        "latency_ms",
        "cost_usd",
        "public_ok",
        "removed_at",
        "payload",
    },
    "trace_spans": {
        "id",
        "trace_id",
        "step",
        "started_at",
        "latency_ms",
        "model",
        "provider",
        "fallback_reason",
        "tokens_in",
        "tokens_out",
        "cost_usd",
    },
    "trace_removals": {"id", "trace_id", "removed_at", "reason"},
    "questions_log": {"id", "trace_id", "created_at", "question_scrubbed", "intent", "outcome"},
    "defense_events": {"id", "created_at", "trace_id", "layer", "category", "action", "example_scrubbed"},
    "log_events": {"id", "at", "level", "source", "component", "event", "trace_id", "fields"},
    "rate_buckets": {"key", "tokens", "refilled_at"},
    "budget_daily": {"day", "cost_usd", "requests", "paid_disabled_at", "degraded_at"},
    "eval_runs": {
        "id",
        "created_at",
        "corpus_version",
        "model_config_version",
        "run_kind",
        "dataset",
        "metrics",
        "passed",
    },
    "eval_results": {
        "id",
        "eval_run_id",
        "question_id",
        "lang",
        "layer",
        "career_fact",
        "passed",
        "reason",
        "expected",
        "answer",
    },
    "external_snapshots": {"id", "taken_at", "source", "kind", "payload"},
    "alert_states": {"kind", "open_since", "last_sent_at", "closed_at"},
}


def _columns(url: str) -> dict[str, set[str]]:
    with psycopg.connect(url) as conn:
        rows = conn.execute(
            "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public'"
        ).fetchall()
    found: dict[str, set[str]] = {}
    for table, column in rows:
        found.setdefault(table, set()).add(column)
    return found


def test_creates_every_f01_table(database_url: str) -> None:
    found = _columns(database_url)
    for table, columns in EXPECTED_COLUMNS.items():
        assert found.get(table) == columns, table


def test_rejects_an_unknown_outcome(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        conn.execute(
            "INSERT INTO public_traces (expires_at, outcome) VALUES (now() + interval '1 day', 'answered')"
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO public_traces (expires_at, outcome) VALUES (now() + interval '1 day', 'other')"
            )
        conn.rollback()
    with psycopg.connect(database_url) as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO questions_log (question_scrubbed, outcome) VALUES ('q', 'other')")


def test_allows_one_active_corpus_version(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        conn.execute("INSERT INTO corpus_versions (status, embed_model_tag) VALUES ('active', 'm')")
        conn.execute("INSERT INTO corpus_versions (status, embed_model_tag) VALUES ('retired', 'm')")
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute("INSERT INTO corpus_versions (status, embed_model_tag) VALUES ('active', 'm')")
        conn.rollback()


async def test_api_reflection_matches_the_migrations(database_url: str) -> None:
    from rag_core.adapters.postgres import Database

    db = Database(database_url)
    await db.reflect()
    try:
        reflected = {name: {c.name for c in table.columns} for name, table in db.metadata.tables.items()}
        for table, columns in EXPECTED_COLUMNS.items():
            assert reflected.get(table) == columns, table
    finally:
        await db.close()


def _one(cursor: psycopg.Cursor[tuple[object, ...]]) -> object:
    row = cursor.fetchone()
    assert row is not None
    return row[0]


def test_delete_rules_live_in_the_database(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        version = _one(
            conn.execute("INSERT INTO corpus_versions (embed_model_tag) VALUES ('m') RETURNING version")
        )
        doc = _one(
            conn.execute(
                "INSERT INTO documents (corpus_version, doc_key, title, kind, lang, authority, hash) "
                "VALUES (%s, 'faq', 'FAQ', 'faq', 'en', 3, 'h') RETURNING id",
                (version,),
            )
        )
        conn.execute(
            "INSERT INTO chunks (document_id, corpus_version, heading_path, content, tokens, hash) "
            "VALUES (%s, %s, 'FAQ', 'text', 1, 'c')",
            (doc, version),
        )
        trace = _one(
            conn.execute(
                "INSERT INTO public_traces (expires_at, outcome) VALUES (now(), 'answered') RETURNING id"
            )
        )
        conn.execute(
            "INSERT INTO questions_log (trace_id, question_scrubbed, outcome) VALUES (%s, 'q', 'answered')",
            (trace,),
        )
        conn.execute(
            "INSERT INTO trace_spans (trace_id, step, started_at, latency_ms) VALUES (%s, 's', now(), 1)",
            (trace,),
        )
        conn.execute("DELETE FROM corpus_versions WHERE version = %s", (version,))
        conn.execute("DELETE FROM public_traces WHERE id = %s", (trace,))
        assert _one(conn.execute("SELECT count(*) FROM documents WHERE id = %s", (doc,))) == 0
        assert _one(conn.execute("SELECT count(*) FROM chunks WHERE document_id = %s", (doc,))) == 0
        assert _one(conn.execute("SELECT count(*) FROM trace_spans WHERE trace_id = %s", (trace,))) == 0
        assert _one(conn.execute("SELECT trace_id FROM questions_log WHERE question_scrubbed = 'q'")) is None
        conn.rollback()
