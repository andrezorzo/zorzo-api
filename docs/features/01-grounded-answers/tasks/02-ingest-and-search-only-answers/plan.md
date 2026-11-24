# Plan for F01-T02: Ingest and search-only answers

Task: [task.md](task.md) · Spec: [spec.md](../../spec.md) · PRD: [prd.md](../../prd.md)

## Criteria

### AC1

**Mark:** integration test

**Scenario:** Given a source folder with one document without `reviewed_by_owner: true`, when the ingest
command runs, then it exits with an error naming the file, the new corpus version is `rejected` and it has
no documents or chunks. The same happens for a document with a secret that gitleaks finds and for a
document with personal data the PII pass finds (an email or phone not in the domain's allowlist). The
secret or personal data itself never appears in the output.

**Planned test:** ingest command run as a subprocess, `tests/ingest/test_ingest.py`, "rejects an
unreviewed document", "rejects a document with a secret", "rejects a document with personal data".

### AC2

**Mark:** integration test

**Scenario:** Given the fixture corpus, when the ingest command runs with the fake embedder, then every
document is split by its Markdown headers into chunks of about `CHUNK_TOKENS`, each chunk stored with its
heading path, embedding, full-text vector and hash, and the version carries the embedder's tag. Given a
second run where one document changed, then only the changed document's chunks are embedded, and the
unchanged ones are copied into the new version with the same embeddings (the run's summary line reports
embedded and copied counts).

**Planned test:** `tests/ingest/test_ingest.py`, "splits by headers and stores embedded chunks", "copies
unchanged chunks into the new version". The Ollama adapter is checked by hand against a local Ollama
(record in this folder, `ollama-check.md`).

### AC3

**Mark:** integration test

**Scenario:** Given a valid candidate, when the ingest command runs with `--activate` and `APP_ENV=local`,
then the candidate becomes `active` and the previous active version `retired`, in one transaction; with
`APP_ENV=production` and no passing eval run, activation is refused, the command exits with an error and
the candidate stays `candidate`.

**Planned test:** `tests/ingest/test_ingest.py`, "activates without an eval run only when local",
"activation retires the previous version".

### AC4

**Mark:** integration test

**Scenario:** Given chunks in the active version and in a retired one, when the vector store searches,
then it returns only active-version chunks; a chunk that matches only by meaning (vector) and one that
matches only by words (full text) both come back, and a chunk matching both ranks first (RRF). Given
those hits, the retrieval service returns them in the reranker's order, cut to `RERANK_TOP_N`; when the
reranker fails, it returns the fused order instead of failing (level "none").

**Planned test:** `tests/core/test_vector_store.py` (Postgres adapter, handcrafted vectors), "searches
only the active version", "fuses vector and full-text hits with RRF"; `tests/core/test_retrieval.py`,
"orders hits by the reranker", "falls back to fused order when the reranker fails";
`tests/core/test_local_reranker.py`, "reranks with the local cross-encoder" (real ONNX model).

### AC5

**Mark:** HTTP test

**Scenario:** Given the ingested fixture corpus, when a visitor sends `POST /api/v1/ask` with `q`,
`session` and `turnstile`, then the response is a stream with `stage` start and end for `retrieve` and
`rerank`, then `degraded {mode: search_only}`, then `retrieval` with at most `DEGRADED_MAX_PASSAGES` hits
from the active version, then `done`; while nothing else is sent the stream carries `: ping` every
`PING_INTERVAL`. A question with no match streams `retrieval` with no hits. A missing field, an empty or
over-long `q` or a `session` that isn't a UUID answers `422 invalid_input` before the stream opens.

**Planned test:** API e2e, `tests/api/test_ask.py`, "streams search-only passages", "streams no hits
when nothing matches", "sends pings while idle", "rejects an invalid request".

### AC6

**Mark:** HTTP test

**Scenario:** Given the committed `contracts/sse-v1.schema.json`, then it equals the schema generated from
the event models and covers every F01 event (`stage`, `guard`, `retrieval`, `model`, `token`, `claim`,
`usage`, `blocked`, `degraded`, `error`, `done`); every event read in any stream test validates against
it, and every stream test checks the ordering rules of LLD "Stream events".

**Planned test:** `tests/contract/test_sse_schema.py`, "committed schema matches the event models",
"schema covers every F01 event"; the shared stream reader in `tests/contract/sse.py` validates each event
and checks the ordering in every stream test; `tests/contract/test_ordering.py` proves the ordering
checker rejects each broken rule (synthetic streams).

### AC7

**Mark:** HTTP test

**Scenario:** Given an extra recording sink attached to the API, when a visitor asks a question, then the
sink receives the same events, in the same order, as the stream sends (pings aside).

**Planned test:** `tests/api/test_ask.py`, "the trace sink receives the streamed events".

### AC8

**Mark:** integration test

**Scenario:** Given an activation, then the local data publisher has written `manifest.json` pointing at a
folder named by content hash, holding `corpus.json` (one entry per chunk of the active version, ids equal
to the database ids, each with a 3D position) and `examples.json` (the domain's example questions with
id, language and text, as in the domain's `examples.yaml`).

**Planned test:** `tests/ingest/test_ingest.py`, "publishes corpus and example data on activation".

### AC9

**Mark:** HTTP test

**Scenario:** Given a database with no active version, when a visitor asks, then the API answers `503
unavailable` before the stream opens; given an active version whose `embed_model_tag` differs from the
API's embedder, the same.

**Planned test:** `tests/api/test_ask.py`, "answers unavailable with no active corpus", "answers
unavailable when the embedding model differs".

## Contract

### POST /api/v1/ask · new

| Item | Shape |
| --- | --- |
| Authentication | Public. The body carries a Turnstile token; verifying it is F01-T04 (gate), so this task checks only that it is present |
| Request body | JSON: `q` string, 1 to `MAX_QUESTION_CHARS` characters after trimming, required; `session` UUID string, required; `turnstile` non-empty string, required. Unknown fields ignored |
| Success | `200 text/event-stream`, events below |
| Response headers | `Cache-Control: no-cache`, `X-Accel-Buffering: no` |
| Errors | `422 invalid_input`; `503 unavailable` (no active version, or its embedding tag differs from the API's); `500 internal`; `405 method_not_allowed` (F01-T04 maps it to the catalog). Body and catalog per LLD "Errors" |

### Stream events · server → client · new

Shared by every event: sent only to the client of that request, as SSE frames `event: <name>` and `data:
<JSON>`; no authentication of its own (the request was accepted); no confirmation; bugs after the stream
opens are the `error` event, never an HTTP error. `ping` is an SSE comment (`: ping`), not an event.

| Event | Payload (all fields required unless marked) | Sent by this task |
| --- | --- | --- |
| `stage` | `name`: `guard` \| `route` \| `cache` \| `retrieve` \| `rerank` \| `assess` \| `reformulate` \| `answer` \| `verify` \| `repair` \| `finish`; `status`: `start` \| `end`; `ms`: integer on `end`, null on `start` | yes |
| `guard` | `layer` string, `label` string, `action` string | no |
| `retrieval` | `hits`: list of `{id: integer (chunk id), title: string, score: number}`; `corpus_version`: integer | yes |
| `model` | `stage` string, `model` string, `provider` string, `fallback_reason` string (optional) | no |
| `token` | `text` string | no |
| `claim` | `i` integer, `text` string, `sources`: list of chunk ids (integers), `verdict`: `supported` \| `partial` \| `unsupported` | no |
| `usage` | `tokens_in` integer, `tokens_out` integer, `cost_usd` number, `latency_ms` integer | no |
| `blocked` | `layer` string, `category` string, `message` string | no |
| `degraded` | `mode`: `search_only`; `reason`: `models_unavailable` \| `budget_exhausted` \| `quota_exhausted` \| `deadline` | yes (`models_unavailable`) |
| `error` | `code` string, `message` string | yes, on a bug |
| `done` | `trace_id` UUID string, `cached` boolean, `abstained` boolean, `suggestion` example id string or null | yes |

Ordering rules: LLD "Stream events", unchanged. In this task the stream is `stage`… → `degraded` →
`retrieval` → `done`.

### chunks · zorzo · altered

| Column | Before | After |
| --- | --- | --- |
| `embedding` | — | `vector(1024)`, not null; index HNSW (`vector_cosine_ops`) |
| `tsv` | — | `tsvector`, not null; index GIN. Built by ingest with the document language's text search configuration (`english` or `portuguese`) over heading path and content |

### answer_cache · zorzo · altered

| Column | Before | After |
| --- | --- | --- |
| `embedding` | — | `vector(1024)`, not null; index HNSW (`vector_cosine_ops`) |

### Published data · local folder (S3 in production) · new

- `manifest.json`: `{corpus_version: integer, embed_model_tag: string, path: "data/<hash>/", published_at:
  ISO 8601 UTC}`. The only file rewritten in place.
- `data/<hash>/corpus.json`: `{corpus_version, chunks: [{id, doc_key, title, kind, lang, heading, text,
  source_url (nullable), p: [x, y, z]}]}`; `p` is the chunk's position from a 3D PCA of its embedding,
  scaled to [-1, 1]. `<hash>` is the SHA-256 of both files' content, so the folder never changes.
- `data/<hash>/examples.json`: `[{id, lang, text}]`.

## Contract verification

- `POST /api/v1/ask` `200` stream: AC5.
- `422 invalid_input`: AC5. `503 unavailable`: AC9. `500 internal` uses the T01 handler, already proven.
  `405`: F01-T04 (AC7 there).
- Each event's payload and the ordering rules: AC6, for every event the stream tests produce; `stage`,
  `retrieval`, `degraded` and `done` by AC5. The events produced by later tasks are validated by the same
  shared reader when those tasks add their stream tests.
- `error` event: AC6 (`tests/api/test_ask.py`, "streams an error then done on a bug", with a failing
  embedder swapped in).
- `chunks.embedding`, `chunks.tsv` and their indexes: AC2 (stored) and AC4 (searched).
- `answer_cache.embedding`: added for F01-T09; checked against the migration at the end of this task, no
  behavior here.
- Published files: AC8.

## Decisions

- **Embedding size 1024, starting with `bge-m3`:** the column is `vector(1024)`. Four multilingual models
  produce 1024 dimensions (`bge-m3`, `multilingual-e5-large`, `snowflake-arctic-embed2`,
  `qwen3-embedding:0.6b`), so the recall comparison at M1 can pick any of them with a re-ingest and no
  migration. The pinned tag starts as `bge-m3:567m` (official Ollama library, 1.2 GB). Settles the vector
  size part of LLD open question "Embedding model".
- **Local rerank model:** fastembed has no built-in `mmarco-mMiniLMv2-L12`, so the adapter registers it as
  a custom model from `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (Apache-2.0), file
  `onnx/model_quint8_avx2.onnx`. Checked on 2026-11-24: 130 MB, about 80 ms for 20 short passages on the
  dev machine, sensible order in Portuguese and English. The model is baked into the API image at build
  and loaded offline (`HF_HUB_OFFLINE=1`).
- **Rerank chain in this task:** local, then none. The provider level (Nemotron) arrives with the model
  gateway (F01-T05). The reranker runs in a thread (`anyio.to_thread`), per LLD "Process model".
- **Hybrid search, one query:** vector top `RETRIEVAL_TOP_K` (cosine, HNSW with
  `hnsw.iterative_scan = relaxed_order` so the version filter doesn't starve results) and full text top
  `RETRIEVAL_TOP_K` (`websearch_to_tsquery` in English OR Portuguese, `ts_rank_cd`), fused by
  `1 / (RRF_K + rank)`, filtered to the given version.
- **Chunking:** `MarkdownHeaderTextSplitter` on `#`, `##`, `###`, then `RecursiveCharacterTextSplitter` at
  `CHUNK_TOKENS` with `CHUNK_OVERLAP`, measured as characters / 4 (no tokenizer dependency; `tokens` stores
  the same estimate). `heading_path` is the document title and the headers joined by " > ".
- **Chunk identity:** `hash = sha256(doc_key, heading_path, content)`, so equal text in two documents stays
  two chunks. A chunk is copied (embedding reused) only from a version with the same `embed_model_tag`;
  with another tag it is embedded again.
- **Front matter:** each corpus file starts with YAML front matter: `doc_key`, `title`, `kind`, `lang`,
  `authority`, `source_url` (optional), `updated` (optional), `reviewed_by_owner`. A missing or invalid
  field rejects the run like a failed scan. `documents.hash` is the SHA-256 of the file.
- **Ingest gate, all or nothing:** the whole source folder is checked before anything is embedded or
  written: front matter, `reviewed_by_owner`, gitleaks (`gitleaks dir --no-git --redact`, behind a
  SecretScanner port) and the PII pass (emails, phone numbers, CPF and card numbers with a Luhn check). Any
  hit marks the candidate `rejected` and the command exits 1, logging the file and the rule, never the
  match.
- **One ingest at a time:** `pg_try_advisory_lock`; a second run exits 1 at once instead of waiting.
- **Ingest command:** `python -m rag_ingest [--source DIR] [--activate]`. The default source is the
  domain package's `corpus/` folder; tests pass the fixture folder. It prints one JSON summary line
  (`version`, `status`, `documents`, `chunks`, `embedded`, `copied`).
- **Publishing on activation:** activation then publishing, in that order; a publish failure leaves the new
  version active and the command exits 1 so it can be re-run (publishing is idempotent by hash). This task
  builds the local DataPublisher (a folder, `DATA_DIR`); the S3 adapter belongs to the deploy work.
- **Search-only flow until the agent exists:** the API embeds the question, searches the active version,
  reranks, and streams `degraded {reason: models_unavailable}` then the top `DEGRADED_MAX_PASSAGES` hits.
  The agent graph (F01-T06) replaces it, and this flow stays as the degraded path.
- **Corpus check per request:** the API reads the active version and its tag on each question (one indexed
  row), so a new activation needs no restart; no active version or a different tag raises `unavailable`
  before the stream opens.
- **Event models are the schema's source:** one Pydantic model per event in `rag_core.events`; `python -m
  rag_core.events` writes `contracts/sse-v1.schema.json` (a `oneOf` over `{event, data}`, with the
  ordering rules in its description), and a test fails when the committed file drifts.
- **TraceSink:** `record(event)` is synchronous and never blocks; the stream sink puts events on the
  request's queue. Later sinks (stored trace, F01-T10; LangSmith) are added to the same fan-out.
- **`done.trace_id`** is the request's `trace_id` from the T01 middleware, so the stored trace (F01-T10)
  reuses it.
- **Fakes in production wiring:** `EMBEDDER=fake` and `RERANKER=fake` are accepted only with
  `APP_ENV=local`; the fake embedder hashes word tokens into a normalized 1024-dimensional vector
  (deterministic, so words in common mean nearby vectors). Seam tests use both fakes; the real local
  reranker has its own test (AC4), and the Ollama adapter a manual check (AC2).
- **Healthz unchanged:** a missing or mismatched corpus shows on `POST /api/v1/ask` (503), not on
  `GET /api/healthz`, which keeps reporting the database and the mode.
- **Example questions** live in the domain's `examples.yaml` (`id`, `lang`, `text`), 4–6 per language.
  The zorzo drafts are proposed in this task and André reviews them in the PR (PRD, first screen).
- **New import rule:** the "only adapters use network clients" contract also forbids `langchain_ollama`,
  `fastembed` and `pgvector` outside `rag_core.adapters`.
- **gitleaks** is pinned and installed in CI, in the ingest image and on the dev machine (prerequisite in
  `apps/ingest/AGENTS.md`); the fastembed model is cached in CI between runs.
- **`config/limits.yaml`:** no task owned it, so this task creates it as a flat
  mapping of snake_case keys (`ping_interval`, `retrieval_top_k`, `rrf_k`, `rerank_top_n`,
  `degraded_max_passages`, `chunk_tokens`, `chunk_overlap`, `max_question_chars`), loaded into a Pydantic
  model at startup (an invalid file stops the app); later tasks add their keys. Settles the `limits.yaml`
  half of the LLD open question about config schemas; `models.yaml` stays with F01-T05.
- **PII allowlist:** the PII pass would reject the owner's own public contact details, so `domain.yaml`
  has a `pii_allowlist` (exact strings, e.g. the public email), empty by default.
- **Local activation without an eval run is permanent:** AC3 says "until the eval gate exists", but local
  development keeps needing it after F01-T11; production never activates without a passing eval run. The
  LLD states it once in "6.1 Corpus version".
- **SSE v1 freezes at launch:** the payloads of events this task doesn't send (`guard`, `model`,
  `blocked`, …) carry strings where the LLD fixes no values. v1 is frozen at launch (2026-12-04); until then
  the task that first sends an event may narrow its payload (enums, new optional fields) in the schema and the
  LLD, since no client depends on it yet.
- **Published data format:** an LLD open question "shared with the front's LLD", which doesn't exist
  yet; this task uses the shape above, based on the hero experiment's `corpus.json`
  (`id`, `title`, `kind`, `heading`, `text`, `p`) without its Phase 2 entities and edges; recorded in the
  LLD under "Interfaces", and the front's LLD points to it.

## Files and interfaces

- `config/limits.yaml`: rule values used here.
- `contracts/sse-v1.schema.json`: generated stream contract.
- `packages/core/src/rag_core/`
  - `events.py`: event models, `StreamEvent` union, `sse_schema()`; `__main__` entry writes the schema.
  - `limits.py`: `Limits` model, `load_limits(path)`.
  - `ports/__init__.py`: `Embedder`, `Reranker`, `VectorStore`, `TraceSink`, `DataPublisher`,
    `SecretScanner` protocols (shapes per LLD "Ports").
  - `services/retrieval.py`: `retrieve(question, version, ...) -> list[Hit]` (embed, search, rerank chain).
  - `services/search_only.py`: `run_search_only(question, sink, ...)`, emits the stream events.
  - `ingest/`: `frontmatter.py` (`DocumentMeta`), `gate.py` (front matter, review flag, PII pass, scanner),
    `chunking.py` (`split(document) -> list[ChunkDraft]`), `pipeline.py` (`run_ingest`, `activate`),
    `publish.py` (builds `corpus.json`, `examples.json`, positions by PCA).
  - `adapters/`: `pgvector_store.py` (`PgVectorStore`: search, upsert, activate, active version),
    `ollama.py` (`OllamaEmbedder`), `fake_embedder.py` (`HashEmbedder`), `local_reranker.py`
    (`LocalReranker`, fastembed custom model), `fake_reranker.py`, `gitleaks.py` (`GitleaksScanner`),
    `local_publisher.py` (`LocalDataPublisher`).
  - `domain.py`: `DomainConfig` gains `pii_allowlist`; `load_examples(name) -> list[Example]`.
  - `settings.py`: `embedder`, `reranker`, `embed_model_tag`, `ollama_url`, `limits_file`.
- `apps/api/src/rag_api/`: `ask.py` (`POST /api/v1/ask`, request model, SSE response with pings);
  `main.py` wires the embedder, reranker, vector store and sinks into `app.state`; settings gain
  `rerank_cache_dir`.
- `apps/ingest/src/rag_ingest/`: `__main__.py` (the command), `settings.py` (`data_dir`); `AGENTS.md`
  with commands and the gitleaks prerequisite.
- `apps/dashboard/data/`: `models.py` gains the vector and full-text fields (pgvector's Django field);
  migration `0003` adds the columns and indexes.
- `domains/zorzo/src/domain_zorzo/`: `examples.yaml` (drafts for André's review), `corpus/` (empty, filled
  by André), `domain.yaml` gains `pii_allowlist`.
- `infra/Dockerfile`: `ingest` target with gitleaks; the `api` target bakes the rerank model.
  `infra/compose.yaml`: `ollama` service (pinned image) and an `ingest` service.
- `.github/workflows/ci.yml`: installs gitleaks; caches the fastembed model.
- `pyproject.toml`: new dependencies (`sse-starlette`, `langchain-ollama`, `langchain-text-splitters`,
  `fastembed`, `pgvector`, `numpy`; dev: `jsonschema`) and the import rule above.
- `tests/`: `fixtures/corpus/` (valid documents) and `fixtures/rejected/` (one folder per refusal case);
  `contract/sse.py` (stream reader: parse, validate, ordering); the test files named in the criteria; a
  session fixture that ingests the fixture corpus with the real command.

## Existing tests that change

- `tests/test_architecture.py`: the network-client contract gains three forbidden modules; the expected
  contract count stays 3.
- `tests/schema/test_migrations.py`: "API reflection matches the migrations" now also sees the vector and
  full-text columns (reflected through pgvector's SQLAlchemy type).

## Data changes

None. The new columns are `NOT NULL` with no default, which is safe because `chunks` and `answer_cache`
are empty in every environment (no corpus has been ingested yet).

## Documents updated

- `docs/lld.md`, section "1. Code structure": the domain's `corpus/` and `examples.yaml` live in the
  `domain_zorzo` package (`contracts/` and `config/limits.yaml` stay planned until the code exists).
- `docs/lld.md`, section "1. Code structure" (libraries): local rerank registered as a fastembed custom
  model (ONNX quint8); embeddings start with `bge-m3:567m`.
- `docs/lld.md`, section "4. Data": `chunks.embedding` and `answer_cache.embedding` are `vector(1024)`.
- `docs/lld.md`, section "5. Interfaces": `POST /api/v1/ask` request body and `503` cases; "Stream events"
  points to `contracts/sse-v1.schema.json` and records the `stage` names, the `degraded` reasons and the
  v1 freeze rule; a new "Published data" subsection with the manifest, `corpus.json` and `examples.json`.
- `docs/lld.md`, section "3. Cross-cutting implementation concepts" (Concurrency): a second ingest run exits
  at once instead of waiting.
- `docs/lld.md`, section "6.1 Corpus version": the gate is all or nothing and never reports the match;
  `pii_allowlist`; chunk hash and copy rule; publishing after activation; activation without an eval run
  only with `APP_ENV=local`.
- `docs/lld.md`, section "9. Values and configuration": `config/limits.yaml` shape; new variables
  `EMBEDDER`, `RERANKER`, `DATA_DIR`, `RERANK_CACHE_DIR`, `LIMITS_FILE`.
- `docs/lld.md`, section "12. Open questions": embedding size settled (model still chosen at M1 among
  1024-dimension models); data format and `limits.yaml` schema settled.
