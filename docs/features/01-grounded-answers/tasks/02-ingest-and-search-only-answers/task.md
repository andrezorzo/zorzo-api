# 02: Ingest and search-only answers

**Status:** to do

**What to build:** A visitor's question returns the best matching passages of the corpus, streamed: the ingest command loads the reviewed Markdown corpus into a new corpus version, and `POST /api/v1/ask` answers in search-only mode with hybrid search and local rerank. This is the first tracer bullet through ingest, database, retrieval and the stream contract.

**Decisions owned:** the SSE schema; the request shape; the TraceSink port and step events; the example questions format and publishing.

**Decisions consumed from other tasks:** The HTTP error body and catalog, and the outcome values (01).

**Blocked by:** 01 API foundation.

- [ ] **AC1** · Ingest refuses documents without `reviewed_by_owner: true` and documents with a gitleaks or PII hit; the candidate becomes `rejected` (LLD "6.1 Corpus version").
- [ ] **AC2** · Ingest splits Markdown by headers, embeds with the pinned tag through the Embedder port (Ollama adapter and a deterministic fake), upserts chunks by content hash and copies unchanged chunks into the new version.
- [ ] **AC3** · Until the eval gate exists (11), a candidate can be activated without an eval run only when `APP_ENV=local`.
- [ ] **AC4** · Retrieval runs vector and full-text search fused by RRF in one query over the active version, then the local rerank level (ADR 0001, ADR 0009).
- [ ] **AC5** · `POST /api/v1/ask` takes `q`, `session` and `turnstile` and streams `stage`, `retrieval`, `degraded` (with its hits first) and `done`, with `ping` while idle (LLD "Stream events").
- [ ] **AC6** · The SSE contract is published as `contracts/sse-v1.schema.json` with every F01 event, including `token`, `claim` (with sources and verdict), `blocked` and `done` with `suggestion`; every event in the tests validates against it, and the ordering rules are contract tests.
- [ ] **AC7** · The TraceSink port receives the step events that feed the stream.
- [ ] **AC8** · The domain's example questions (ids, text, language) are published with the brain and corpus data through the DataPublisher, so the front and `done.suggestion` share the same ids.
- [ ] **AC9** · The API with an empty or mismatched corpus answers `unavailable` (503) per LLD "6.1 Corpus version".
