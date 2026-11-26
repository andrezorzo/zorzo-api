---
status: accepted
---

# 0013 · Our own chunk tables in the Django migrations, not langchain-postgres

LangChain's `langchain-postgres` (PGVector) creates and manages its own vector table, with metadata in JSON
and collections instead of versions. The system needs three things it doesn't give: the table inside the
Django migrations, which own the schema; a corpus version that is swapped atomically, with exactly one active
(ADR 0001); and hybrid search, vector plus full-text plus RRF, in one SQL statement over typed columns. So
`documents` and `chunks` are ordinary Django models, with a `vector(N)` column (HNSW) and a `tsvector` column
(GIN), and the `PgVectorStore` adapter writes the hybrid query itself.

Discarded: `langchain-postgres` managing the vector table (less code to start, but a table outside the
migrations, versioning bolted onto JSON metadata, and full-text search outside the same query).

Cost accepted: the store adapter and its SQL are ours to write and test; LangChain retriever conveniences are
not used directly (the adapter can still expose a LangChain retriever interface if a tool needs it).
