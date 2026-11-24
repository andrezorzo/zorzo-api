---
status: accepted
---

# 0001 · One Postgres with pgvector for vectors, full-text, rate limits and state

The corpus is a few thousand chunks and traffic is a few questions per minute, on one VPS with 8 GB of RAM
and a budget under US$10/month. One Postgres 17 with pgvector holds chunks, embeddings, the full-text
index, rate-limit counters, budget, traces, eval runs and conversation state. Three reasons: hybrid search
(vector + full-text + RRF fusion) runs as a single SQL query; activating a new corpus version (chunks,
embeddings and full-text together) is one transaction, so the API never serves new vectors with old text;
and there is one stateful service to back up, upgrade and monitor, next to Ollama, FastAPI and Django on
the same machine.

Discarded: Postgres + Qdrant (better vector features and scale, but two systems, hybrid search split into
two queries merged in code, and corpus swaps no longer atomic); Postgres + Redis for rate limits, cache and
sessions (faster, but at this traffic `INSERT … ON CONFLICT` counters are enough, the semantic answer cache is a
pgvector lookup in the same database, and session state lives in the LangGraph checkpointer); a managed vector database such as Pinecone (no
operations, but free-tier limits or cost, data outside the VPS, and hybrid search still split).

Cost accepted: at tens of millions of vectors or heavy filtered search, pgvector falls behind a dedicated
vector database, and rate limiting costs one database write per request. The way out is designed in:
retrieval sits behind a store interface, and the M5 benchmark compares pgvector with Chroma on recall,
latency and cost.

Interview answer: "One stateful service. Hybrid search is one SQL query, corpus swaps are atomic, and
there is one backup. At a few thousand chunks a dedicated vector DB adds operations, not recall."
