"""The stored trace of a finished request (LLD "3. Cross-cutting implementation concepts", "Transactions")."""

import json

from sqlalchemy import text

from rag_core.adapters.postgres import Database
from rag_core.services.trace import StoredTrace

INSERT_TRACE = text("""
INSERT INTO public_traces (id, expires_at, outcome, intent, lang, corpus_version, model_config_version,
                           latency_ms, cost_usd, public_ok, payload)
VALUES (:id, :expires_at, :outcome, :intent, :lang, :corpus_version, :model_config_version,
        :latency_ms, :cost_usd, :public_ok, CAST(:payload AS jsonb))
""")
INSERT_SPAN = text("""
INSERT INTO trace_spans (trace_id, step, started_at, latency_ms, model, provider, fallback_reason,
                         tokens_in, tokens_out, cost_usd)
VALUES (:trace_id, :step, :started_at, :latency_ms, :model, :provider, :fallback_reason,
        :tokens_in, :tokens_out, :cost_usd)
""")
INSERT_QUESTION = text("""
INSERT INTO questions_log (trace_id, question_scrubbed, intent, outcome)
VALUES (:trace_id, :question_scrubbed, :intent, :outcome)
""")


class PostgresTraceStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def write(self, trace: StoredTrace) -> None:
        """The trace, its spans and its questions-log row in one transaction: all or none. A failing
        database raises `StoreUnavailableError`."""
        trace_id = str(trace.trace_id)
        async with self.db.short_transaction() as conn:
            await conn.execute(
                INSERT_TRACE,
                {
                    "id": trace_id,
                    "expires_at": trace.expires_at,
                    "outcome": trace.outcome,
                    "intent": trace.intent,
                    "lang": trace.lang,
                    "corpus_version": trace.corpus_version,
                    "model_config_version": trace.model_config_version,
                    "latency_ms": trace.latency_ms,
                    "cost_usd": trace.cost_usd,
                    "public_ok": trace.public_ok,
                    "payload": json.dumps(trace.payload, ensure_ascii=False),
                },
            )
            if trace.spans:
                await conn.execute(
                    INSERT_SPAN,
                    [
                        {
                            "trace_id": trace_id,
                            "step": s.step,
                            "started_at": s.started_at,
                            "latency_ms": s.latency_ms,
                            "model": s.model,
                            "provider": s.provider,
                            "fallback_reason": s.fallback_reason,
                            "tokens_in": s.tokens_in,
                            "tokens_out": s.tokens_out,
                            "cost_usd": s.cost_usd,
                        }
                        for s in trace.spans
                    ],
                )
            # Last: the row the log keeps after the trace expires, so its failure leaves nothing behind.
            await conn.execute(
                INSERT_QUESTION,
                {
                    "trace_id": trace_id,
                    "question_scrubbed": trace.question_scrubbed,
                    "intent": trace.intent,
                    "outcome": trace.outcome,
                },
            )
