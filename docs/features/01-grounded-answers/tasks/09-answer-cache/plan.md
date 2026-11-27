# Plan for F01-T09: Answer cache

Task: [task.md](task.md) · Spec: [spec.md](../../spec.md) · PRD: [prd.md](../../prd.md)

The task adds the semantic answer cache of ADR 0014 to both answer paths. In normal mode a `cache` step
between `route` and `retrieve` looks up the standalone question; a hit replays the verified claims stored
from an earlier run and skips retrieval, the answer and the judge. In degraded mode the first question of a
conversation is looked up with the raw question and a local language detector before search-only. An
answered run stores its claims after `done`. `model_config_version` starts hashing the prompts (the gap the
F01-T14 plan left to this task) and the intents, so a prompt or intent change empties the cache in effect.
The `answer_cache` table already exists; its columns don't change. By the agreed merge order (T10 → T08 →
T07 → T09 → T11), this task lands on top of the guard, the contact reply, the conversation state and the
stored trace.

## Criteria

### AC1

**Mark:** HTTP test

**Scenario:** Given a question the corpus covers, when it is answered and verified, then once the write ends
`answer_cache` has one row with the standalone question, its language, the active corpus version, the app's
`model_config_version`, the claims exactly as the stream sent them (verdicts and the `gap` sentence included),
`expires_at` `CACHE_TTL` after its `created_at`, and `trace_id` null. Given an abstention, a decline, a
contact reply, a block, a degraded answer, a hit, or a question in a language the domain doesn't declare, then
no row is written. Given a standalone question with personal data (an email address), then there is no `cache`
stage and no row. Given the stored row, when the same question is asked again with other case and punctuation,
then it is a hit. When a question with one more word is asked (similarity below `CACHE_SIMILARITY` with the
fake embedder), it is a miss and the full graph runs. Given the cache store failing, then the question is
answered as a miss and a `warning` line is logged.

Given rows that differ from a lookup in only one of language, corpus version or `model_config_version`, or
that expired, when the adapter looks up, then none matches; given two matching rows, the nearer one is
returned and its `hits` goes up by one. Given the same models and limits with one prompt changed (the
`route` prompt through an intent description too), or one intent's document kinds changed,
`config_version` changes; formatting doesn't change it, and the canary never enters it.

**Planned test:** e2e of the API, `tests/api/test_cache.py`, "an answered question stores its claims",
"only verified answers are stored", "an almost identical question gets the stored answer", "a question in
another language skips the cache", "a question with personal data skips the cache", "a failing cache
store is a miss"; integration,
`tests/core/test_answer_cache.py`, "a hit needs the same language and versions", "an expired entry never
matches", "the nearest entry wins and counts the hit"; unit, `tests/core/test_models_config.py`, "the
version follows the prompts and intents", "the canary never enters the version".

### AC2

**Mark:** HTTP test; Playwright test (zorzo-site)

**Scenario:** Given a stored answer, when the same question is asked, then the stream has the `route` and
`cache` stages, the `model` event of `route`, then one `claim` per stored claim with the same text, sources
and verdict as the first run, `usage` with `route`'s cost only (0 here: the fake's `route` entry is free),
and `done {outcome: answered, cached: true}` with the contact options and a null `suggestion`; it has no
`token`, no `retrieval`, no `retrieve`, `answer` or `verify` stage and no `model` event besides `route`'s,
and the fake provider gets no `answer` or judge call. The stream validates against the SSE schema and
passes the ordering rules. Given a stream marked cached that has a `token`, or a `model` event of the
`answer` or `judge` stage, then the ordering check rejects it; one whose `usage` carries `route`'s paid cost
passes. Given a
recorded cached stream in zorzo-site, when the page plays it, then the steps list shows "reused a verified
answer to the same question" (Portuguese: "reaproveitou uma resposta verificada para a mesma pergunta"),
the answer is labeled as reused, and a citation opens its passage with the title from `corpus.json`, with no
`retrieval` event in the stream.

**Planned test:** e2e of the API, `tests/api/test_cache.py`, "a hit replays the verified claims with no
token"; unit, `tests/contract/test_ordering.py`, "rejects a cached answer with tokens or answer models"
(replaces "rejects a cached answer with tokens or cost"); Playwright, `zorzo-site/tests/e2e/answer.spec.ts`,
"a reused answer is labeled and its citations open".

### AC3

**Mark:** HTTP test; document

**Scenario:** Given an answer stored in normal mode and then the day's budget at the cap (degraded), when a
new conversation asks the same question, then the stream has the `cache` stage, the stored claims, `usage`
with cost 0 and `done {outcome: answered, cached: true}`, no `degraded` event, and the fake provider gets no
call at all. Given a question the detector reads as Spanish, one with personal data, or one with no stored
match, then the stream is search-only as today (`degraded`, its `retrieval`, `done`), with no model call.
Given a conversation with an earlier turn and then degraded mode, when its follow-up repeats a stored
question, then it goes straight to search-only, with no `cache` stage. Given a session idle past
`SESSION_TTL`, its next question counts as a first question again. The language detector adapter detects
English and Portuguese questions with the network blocked. For the document half: LLD section 1 names the
detector library and why, and section 6.9 points to it.

**Planned test:** e2e of the API, `tests/api/test_cache.py`, "degraded mode serves a first question from
the cache", "degraded mode without a match is search-only", "degraded mode skips a language the domain
doesn't declare", "a degraded follow-up goes straight to search-only"; unit, `tests/core/test_language.py`,
"detects the domain's languages offline".

### AC4

**Mark:** HTTP test

**Scenario:** Given `RATE_SESSION_BURST` of 2 and a question answered once, when the same question is asked
again (a hit) and then a third time, then the third gets `429 rate_limited`: the hit took a token.

**Planned test:** e2e of the API, `tests/api/test_cache.py`, "a hit still counts against the rate limit".

## Contract

### POST /api/v1/ask · altered

Status codes, errors and the request body don't change. No event's fields change: `stage` already allows
`cache`, `done.cached` already exists (the agreed `done` fields give it to this task), and `contact` in
`done` is F01-T08's. Only the ordering text in `rag_core.events.ORDERING` changes (the cached-answer rule
below), so `contracts/sse-v1.schema.json` is regenerated for its `description`, and zorzo-site's copy in
`tests/fixtures/` follows.

| Item | Before | After |
| --- | --- | --- |
| Normal mode, miss | `stage` guard, route, retrieve, rerank, … | `stage` guard, route, **cache**, retrieve, rerank, …; the `cache` stage only when the question's language is one of the domain's and it has no personal data |
| Normal mode, hit | — | `stage` (guard, route, cache), `model` (route), the stored `claim` events, `usage` (`route`'s cost only), `done {outcome: answered, cached: true, contact, suggestion: null}` |
| Degraded mode, first question in a domain language | `stage` retrieve, rerank, `degraded`, `retrieval`, `done` | `stage` cache, then either the hit's `claim` events, `usage` (cost 0) and `done {outcome: answered, cached: true}`, or the search-only events as before |
| `done.cached` | Always `false` | `true` on a hit, `false` otherwise |
| Ordering rule for a cached answer | `usage` cost 0, no `token` | No `token` and no `model` event besides `route`'s; cost is `route`'s (0 unless it ran on the paid entry) |

### answer_cache · zorzo · first writer

No column, index or constraint changes. What the writer stores in the existing columns:

- `question_scrubbed`: the standalone question, which by rule has no personal data (in degraded mode
  nothing is written).
- `lang`: the route's language, one of the domain's.
- `corpus_version`, `model_config_version`: the run's.
- `answer`: `{"claims": [{"i", "text", "sources", "verdict"}, …]}`, the `claim` payloads as sent, in order.
- `embedding`: the vector the lookup computed.
- `expires_at`: `now() + CACHE_TTL`, by the database clock; `hits` starts at its default 0.
- `trace_id`: null; F01-T10 decides whether the cache write moves inside its end-of-request transaction.

**Rule values (`limits.yaml`), new:** `cache_similarity: 0.95`, `cache_ttl: 5184000` (seconds, 60 days),
both from LLD section 9.

**Dependency:** `fast-langdetect==1.2.0` (published 2026-11-13), in `packages/core`.

## Contract verification

- Normal-mode hit events, `done.cached: true` and the cached-answer ordering rule: AC2.
- The `cache` stage on a miss, and its absence for another language or personal data: AC1.
- Degraded-mode hit with no `degraded` event, and the search-only fallback: AC3.
- `answer_cache` rows written only for verified answers, with the stored fields above: AC1.
- The lookup's filters (language, versions, expiry, threshold, nearest wins, `hits`): AC1.
- The hit taking a rate-limit token: AC4.
- The two rule values: loaded and validated by `Limits` at startup, which every API test exercises; their
  effect is proven by AC1 (threshold, expiry). Not a criterion of their own: configuration.

## Decisions

- **Library before own code.** Each candidate checked in its installed code (LiteLLM, langchain-core and
  LangGraph from the project's lockfile; the others in a scratch environment):
  - *LiteLLM 1.108.5 caching* (installed): `LiteLLMCacheType` is `local`, `redis`, `redis-semantic`,
    `valkey-semantic`, `s3`, `disk`, `qdrant-semantic`, `azure-blob`, `gcs`. No Postgres backend, and it
    caches one model call by its request: a hit would still run the judge, and what it caches is a draft,
    not verified claims. Discarded.
  - *langchain-core 1.8.4 `BaseCache`* (installed): `lookup(prompt, llm_string)`, an exact match per model
    call. Its semantic versions live in other packages for Redis, MongoDB, Cassandra and others, none for
    pgvector. Discarded for the same per-call reason.
  - *LangGraph 1.2.21 node cache* (installed): `CachePolicy(key_func, ttl)`, whose key defaults to a pickle
    hash of the node's input, with memory and Redis backends. An exact key, not similarity. Discarded.
  - *langchain-postgres 0.0.19*: vector stores and chat history, no cache class (ADR 0013 already keeps the
    chunks out of it). Discarded.
  - *GPTCache 0.1.44*: last release 2024-08-01, with its own storage layers. Unmaintained. Discarded.
  - *RedisVL `SemanticCache`*: needs Redis with vector search, which ADR 0014 rejected. Discarded.
  - What stays ours: one lookup query and one insert in a Postgres adapter, plus the rules of LLD 6.9 in a
    service. The cache stores a whole verified run, keyed by the standalone question with filters, and no
    library caches at that level.
- **The local language detector is fast-langdetect with its `lite` model.** Measured on 39 short questions
  in English, Portuguese, Spanish, French, German and Italian:
  - *fast-langdetect 1.2.0, `lite`*: 36 of 39, about 1 MB, 0.8 ms a question. It names 176 languages, so
    another language reads as itself and not as English or Portuguese. Confirmed in its code and with the
    network blocked: `model="lite"` loads the bundled `lid.176.ftz` and never downloads. The default `auto`
    downloads the 126 MB model at the first call, so the adapter always passes `lite`. It also passes
    `max_input_length=None`: the default 80-character cut logs a line on each long question. The model is
    CC BY-SA 3.0; the image ships it unmodified, with the package's notice. Chosen.
  - *lingua-language-detector 2.2.0*: 36 of 39 when limited to the six languages and 33 of 39 with all of
    them, but its wheel is 292 MB in the API image, and the limited mode needs a hand-picked list of "other"
    languages. Discarded.
  - *py3langid 0.4.0*: 13 of 16 on the first sample, 25 ms a question, 137 MB resident. Discarded.
  - *langdetect, langid, pycld3, gcld3*: no release since 2016 to 2021. Discarded.
  A wrong detection costs only a miss: the lookup filters by language, so a misread question gets search-only,
  as it would without the cache.
- **The lookup is a graph step, `cache`, between `route` and `retrieve`.** The agreed graph order is guard
  (from `START`) → route → cache → retrieve. The step runs where `retrieve` would, so a run that ended before
  it never looks up and is never stored: a block, a decline, F01-T08's contact reply, F01-T07's endings. It
  sends its `stage` on every lookup, so the trace has its latency. It is skipped, with no `stage`, when the
  route's language isn't one of the domain's or the standalone question has personal data. Its per-run state
  keys (`query_vector`, `cached`) are `UntrackedValue`, like every per-run key, so F01-T07's checkpointer
  never carries them into the next turn.
- **Other languages skip the cache.** The answer to a question in an undeclared language carries the line
  naming the site's languages (LLD 6.3 "Edge questions", F01-T07). Keying it under the answer's language
  would serve an English question that line, or a Spanish question an answer without it. So such questions
  are neither looked up nor stored, and the stored language is always both the question's and the answer's.
- **The key is the standalone question's embedding, computed once.** The pinned embedder embeds the
  standalone question at lookup; on a miss the vector waits in the run's state for the row it may store.
  Retrieval keeps embedding its own query, since a reformulated query differs. A follow-up hits through its
  standalone question.
- **One statement per lookup.** A CTE takes the nearest row under the filters (`lang`, `corpus_version`,
  `model_config_version`, `expires_at > now()`) by cosine distance. The `UPDATE … RETURNING` adds one to its
  `hits` only when the similarity reaches `CACHE_SIMILARITY`. It runs in `Database.short_transaction`
  (statement and lock timeouts) with `SET LOCAL hnsw.iterative_scan = strict_order`: the relaxed order the
  corpus search uses may return a farther row first, and with `LIMIT 1` that would be the one served.
- **A hit replays what the visitor of the first run saw.** The stored claims are the `claim` payloads as sent,
  `unsupported` (shown as removed) and `gap` included; each is validated as a `Claim` when read, and a row
  that no longer validates (a later change to the event) is a miss. Claims cite chunk ids, which stay valid
  because a hit needs the same corpus version. No `retrieval` is sent (AC2), so the front takes citation
  titles from `corpus.json`, and the brain doesn't light up: ADR 0014's accepted cost.
- **What is stored, and when.** Only a run whose graph ended normally as `answered` with `claims` from
  `verify` is stored. A run cut after its claims (the deadline, a bug) is not stored, nor is a hit or any path
  that didn't verify. The write is spawned on the cache service's `BackgroundTasks` after `done`, so it never
  holds the stream. A visitor who leaves earlier cancels the run, and nothing is stored. The service drains
  its writes in the lifespan before the database closes, within `SHUTDOWN_DRAIN`. Concurrent misses of one
  question may store two rows; the lookup serves the nearer, and the copy is harmless. The row's `trace_id`
  stays null; F01-T10 decides whether the write moves into its end-of-request transaction to set it.
- **The cache never fails a question.** A lookup that raises or runs past the steps' deadline
  (`before_answer`) is a miss; a failed write is dropped. Each is logged at `warning` with the error type
  only (`cache_lookup_failed`, `cache_write_failed`), never the question.
- **Expiry.** `expires_at` is set at write, and the lookup ignores expired rows. Deleting them is the
  retention job's (LLD 6.11), which no F01 task builds yet; until it exists, expired rows only take space.
- **`model_config_version` hashes the prompts and the intents.** `config_version(models, limits, prompts,
  intents)` adds the loaded `Prompts` (the `route` prompt as rendered with the intents, the others as
  templates) and the domain's intents (description, `kinds`, `action`) to the canonical JSON. A prompt edit,
  a new intent description or an intent's new document kinds then changes the version, and old entries stop
  matching. The prompts are hashed as loaded from the domain package, before F01-T08 adds the canary line at
  request time: the canary is a secret and never enters the version, and a test proves it. F01-T10 records
  the version and F01-T11 stores it with eval runs; both read the new contents, and neither computes it
  differently. The whole limits file stays in it, as the LLD says, so a rate-limit change also empties the
  cache. Narrowing it to the answer-affecting values would need a list to keep in sync, and the cache refills
  from free questions.
- **The degraded path.** `ask.py` calls `run_degraded`, which asks the cache service before search-only when
  the question is the first of its conversation (`load_conversation`, below). It detects the raw question's
  language, and a language outside the domain's, or personal data in the question, goes straight to
  search-only. Otherwise it embeds the question with the same pinned embedder, looks up, and on a hit sends
  `stage cache`, the claims, `usage` (all zeros but latency) and `done` (with the contact options and a null
  `suggestion`, as every cached `done`). A miss continues as today. Everything that can fail runs before the
  first `claim`, so a failure is a miss and search-only follows. The embedder is local (Ollama), so degraded
  mode still has it.
- **No front change beyond the reused label and titles.** The steps list adds the reused line when `done`
  has `cached: true`, and the answer panel shows a "reused" label. The `cache` stage end prints like any
  stage. Citation titles fall back to `corpus.json` for ids not in a `retrieval` event.
- **`RunResult` gains `cached`.** `run_agent` sets `done.cached` from the run's state, and returns it for
  the stored trace: F01-T10 maps `done.cached` to the stored outcome `cached`.
- **A cached `done` follows the agreed `done` fields.** `cached` is this task's field, `true` on a hit and
  `false` otherwise. A cached `done` also carries F01-T08's `contact` (the domain's contact options, as after
  every answer), and its `suggestion` is null.
- **Merge order.** The agreed order is T10 → T08 → T07 → T09 → T11, so this task rebases on the stored
  trace, the guard and contact reply, and the conversation state. Its stage-order and degraded-stream test
  changes are written against their streams.
- **Tests empty `answer_cache` before each API test.** The fixture corpus database is shared by the
  session, and the tests reuse the same question, so a row left by one test would turn the next one's
  answer into a hit.
- **A hit's `usage` reports what the request spent** (owner's decision). A hit still runs `route`, normally
  on a free entry (cost 0). When every free `route` entry is cooling and the paid one answers, the request
  has a real cost, and the visitor's counter shows it; degraded mode makes no model call, so its hits always
  cost 0. AC2's text changed to "`usage` with no answer or judge cost (0 unless `route` ran on the paid
  entry)" (owner-approved change of the AC text, 2026-11-27). LLD "Stream events" and the check in
  `tests/contract/sse.py` now say "no `token` and no `model` event besides `route`'s" instead of "cost 0".
  Discarded: always reporting 0, which would understate that request on the counter while the budget counts
  it.
- **Degraded follow-ups read F01-T07's history** (owner's decision, after the agreed merge order). "The first
  question of a conversation" means a session with no earlier turn, which lives in F01-T07's checkpointer.
  F01-T07 merges before this task, so `run_degraded` calls its helper `load_conversation(agent, session, now,
  limits)` and looks up only when the conversation has no earlier turn; a session idle past `SESSION_TTL`
  starts over there, so its next question counts as a first one. A failure to load the conversation goes to
  search-only, never to a hit served without its context. This task proves both halves of AC3, and 07 is not
  added to "Blocked by" (the merge order already puts it first).
- **Questions with personal data skip the cache** (owner's decision). ADR 0014 keys the cache by the
  "scrubbed" standalone question, but no document defines scrubbing, and the shared redactor is F01-T10's.
  So a standalone question (or, in degraded mode, a raw question) in which the ingest's PII pass
  (`personal_data` in `rag_core.ingest.gate`: emails, phones, CPF, card numbers, with the domain's
  `pii_allowlist`) finds anything is neither looked up nor stored. Every stored question then has nothing to
  scrub, and this task builds no scrubber. Such an entry would hardly match a second visitor anyway, and it
  would be the riskiest row to keep. LLD 6.9 has the rule.

## Files and interfaces

- `packages/core/pyproject.toml`, `uv.lock`: `fast-langdetect==1.2.0`.
- `pyproject.toml`: import-linter, `fast_langdetect` only in `rag_core.adapters`.
- `config/limits.yaml`, `packages/core/src/rag_core/limits.py`: `cache_similarity` (0 < x ≤ 1),
  `cache_ttl` (seconds, > 0).
- `packages/core/src/rag_core/models_config.py`: `config_version(models, limits, prompts, intents)`.
- `packages/core/src/rag_core/ports/__init__.py`: `CachedAnswer(claims, similarity)`, `CacheEntry`;
  `AnswerCache` with `lookup(vector, lang, corpus_version, model_config_version, min_similarity) ->
  CachedAnswer | None` and `store(entry, ttl_s) -> None`; `LanguageDetector.detect(text) -> str | None`.
- `packages/core/src/rag_core/adapters/answer_cache.py` (new): `PgAnswerCache(db)`, the lookup statement
  and the insert.
- `packages/core/src/rag_core/adapters/language.py` (new): `FastTextLanguageDetector`, with
  `LangDetectConfig(model="lite", max_input_length=None)` and `warm()` loading the model at startup.
- `packages/core/src/rag_core/services/answer_cache.py` (new): `AnswerCacheService(store, embedder, limits,
  model_config_version, languages)` with `lookup(question, lang, corpus_version) -> Lookup` (the claims or
  the vector of a miss; None when skipped), `remember(question, lang, corpus_version, vector, claims)`
  (spawns the write), `replay(sink, claims)`, `drain(timeout)`; the personal-data skip through
  `personal_data` from `rag_core.ingest.gate`, with the domain's `pii_allowlist`.
- `packages/core/src/rag_core/agent/state.py`: `query_vector`, `cached`, both `UntrackedValue`.
- `packages/core/src/rag_core/agent/context.py`: `cache: AnswerCacheService`.
- `packages/core/src/rag_core/agent/nodes.py`: `cache_step`.
- `packages/core/src/rag_core/agent/graph.py`: the `cache` node and the edge `route → cache → retrieve`,
  after F01-T08's guard and contact branches; `run_agent` sets `done.cached`, calls `remember` after `done`
  on a stored-worthy run; `RunResult.cached`.
- `packages/core/src/rag_core/services/search_only.py`: `run_degraded(question, corpus, trace_id, *, cache,
  detector, agent, session, …)`, which calls F01-T07's `load_conversation(agent, session, now, limits)`,
  tries the cache for a first question, then `run_search_only`.
- `apps/api/src/rag_api/main.py`: wires `PgAnswerCache`, the detector (warmed in the lifespan) and the
  service with `model_config_version` from `config_version(models, limits, prompts, intents)`; drains the
  service at shutdown before the database closes.
- `apps/api/src/rag_api/ask.py`: the context gets the cache; degraded mode calls `run_degraded` with the
  agent and the session.
- `tests/api/test_cache.py`, `tests/core/test_answer_cache.py`, `tests/core/test_language.py` (new).
- `tests/contract/sse.py`: the cached-answer rule, no `token` and no `model` event besides `route`'s.
- `packages/core/src/rag_core/events.py`: the same rule in `ORDERING`; `contracts/sse-v1.schema.json`
  regenerated, and `zorzo-site/tests/fixtures/sse-v1.schema.json` with it.
- `tests/conftest.py`: an autouse fixture that empties `answer_cache` in API tests; a helper that waits for
  the cache writes (`client.portal.call(app.state.answer_cache.drain)`).
- `zorzo-site`: `assets/js/ui/ask.js` (the reused line and label on `done.cached`; citation titles from
  `corpus.json`), `assets/js/i18n.js` (both languages), `assets/styles.css` (the label),
  `tests/fixtures/streams/cached.sse`, `tests/e2e/answer.spec.ts`.

## Existing tests that change

- `tests/api/test_answer.py`, "a covered question runs the bounded steps in order" and any assertion on the
  full stage list: `cache` between `route` and `retrieve`.
- `tests/api/test_degraded.py`, `tests/api/test_ask.py`: degraded streams of a question in a domain language
  now start with the `cache` stage; assertions on the exact event list change.
- `tests/core/test_models_config.py`, `tests/api/test_startup.py`: `config_version` takes the prompts and
  the intents.
- `tests/contract/test_sse_schema.py`: the drift test passes again after the schema is regenerated.
- `tests/contract/test_ordering.py`, "rejects a cached answer with tokens or cost": becomes "rejects a cached
  answer with tokens or answer models"; a cached stream whose `usage` has `route`'s paid cost now passes.
- `zorzo-site/tests/e2e/streams.spec.ts`: validates the new recorded stream with the others.

## Data change

None. The table is empty, and no row needs a backfill. The first deploy changes `model_config_version`
(prompts, intents and the two new limits), which matches nothing stored.

## Updated documents

- `docs/lld.md`:
  - "1. Code structure", stack table: new "Language detection" row (fast-langdetect, `lite` model, why).
  - "2. Components and dependencies", ports: `AnswerCache` (`lookup`, `store`) and `LanguageDetector`.
  - "Configuration": `model_config_version` hashes the prompts (the `route` prompt as rendered with the
    intents) and the domain's intents, never the canary; the shutdown drain covers the cache's writes.
  - "4. Data": the `answer_cache` note says what `answer` holds.
  - "5. Interfaces", "Stream events": the cached-answer rule, no `token` and no `model` event besides
    `route`'s, with `route`'s cost only.
  - "6.9 Answer cache": the `cache` step and its `stage`; other languages and questions with personal data
    skip the cache; the key; the nearest entry and `hits`; what a hit streams (its cost, and `done` with
    `contact` and a null `suggestion`); what is stored and when (contact replies never); failures are
    misses; expiry and the retention job; the degraded path with the detector, what a first question is
    (no earlier turn; an idle conversation starts over; an unreadable one is a follow-up) and no `degraded`
    event on a hit.
  - "9. Values and configuration": `SHUTDOWN_DRAIN` also bounds the cache's writes.
- `docs/features/01-grounded-answers/spec.md`, "Interactions worth stating": the cached answer's `usage` has
  no answer or judge cost (0 unless `route` ran on the paid entry), as AC2 now says.
- `docs/hld.md`: no change.
- `zorzo-site/docs/lld.md`: the reused label and the citation title fallback, in the zorzo-site PR.
