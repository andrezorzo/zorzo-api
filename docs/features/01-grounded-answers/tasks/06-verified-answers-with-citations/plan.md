# Plan for F01-T06: Verified answers with citations

Task: [task.md](task.md) · Spec: [spec.md](../../spec.md) · PRD: [prd.md](../../prd.md)

The task replaces the search-only path of normal mode with the bounded agent graph (ADR 0006). Search-only
stays as the degraded path. Conversation history, other languages and answer length are F01-T07; guard
layers, decline wording, suggestions and the `contact` intent are F01-T08; the cache is F01-T09; the stored
trace is F01-T10.

## Criteria

### AC1

**Mark:** integration test

**Scenario:** Given the fake provider answering the `route` stage with `{intent, standalone_question,
language, follow_up, public_ok}`, when the route service runs on a question, then it returns those five
fields after one request to the `route` chain, sent with the output schema (`response_format`). Given
fenced JSON (```` ```json … ``` ````), it is read the same way. Given an intent outside the enum, the
gateway counts a model miss and moves on as for any schema miss.

**Planned test:** integration, `tests/core/test_route.py`, "route returns the five fields in one call",
"route reads fenced JSON", "an unknown intent is a schema miss".

### AC2

**Mark:** HTTP test

**Scenario:** Given a corpus that covers the question, when a visitor asks, then the stream's `stage`
events run route → retrieve → rerank → assess → answer → verify, in that order, each with a start and an
end. Given `assess` saying the evidence is insufficient, then one `reformulate` runs, retrieval runs again
with its query, and `answer` follows with no second `assess`. Given a first verification with nothing
supported, then one `repair` and a second `verify` run, and a second failure ends as an abstention. Given
less than `ANSWER_RESERVE` left when `answer` would start, then the stream is search-only with `degraded
{reason: deadline}`. Given `route` failing on every entry, then the stream is search-only with
`degraded {reason: models_unavailable}`. Given a graph that loops past `RECURSION_LIMIT` (a test graph
built with a looping edge), then the run ends as an abstention and an `error`-level log line
`recursion_limit` is written.

**Planned test:** e2e of the API, `tests/api/test_answer.py`, "a covered question runs the bounded steps in
order", "weak evidence gets one reformulate", "a failed verification gets one repair, then abstains", "too
little time for the answer goes search-only", "route unavailable goes search-only"; unit,
`tests/core/test_graph.py`, "the recursion backstop abstains and logs".

### AC3

**Mark:** HTTP test

**Scenario:** Given an answer whose claims cite passage numbers 1 and 2 of this request's passages, when the
judge labels them `supported` and `partial` (with the supported part), then the stream has one `claim` per
sentence with the chunk ids of its passages and its verdict, and the partial one carries the supported
part's text. Given a claim citing a passage number outside the request's passages, or none, then it is
sent as `unsupported` with no judge vote. Given a draft that is not valid JSON, then the answer is asked
again through the schema-bound call and its claims are verified. Given the judge failing on every entry,
then no claim is sent and the answer is an abstention: verification is never skipped.

**Planned test:** e2e of the API, `tests/api/test_answer.py`, "claims carry their sources and verdicts",
"a citation outside the request's passages removes the claim", "an unreadable draft is asked again with
the schema", "a judge that can't run turns the answer into an abstention".

### AC4

**Mark:** HTTP test

**Scenario:** Given a covered question, then the stream sends `retrieval`, then `token` events whose
concatenation is the claims' text (no JSON, no passage numbers), then `claim` events, `usage` with the
summed tokens and cost and the request's latency, and `done {outcome: answered}`; no `token` follows the
first `claim`, and every event validates against `contracts/sse-v1.schema.json`. Given the judge hanging
past the deadline, then the stream sends no `claim`, and ends with `usage` and `done {outcome: abstained}`
after the draft.

**Planned test:** e2e of the API, `tests/api/test_answer.py`, "the draft streams before the checked
claims", "verification out of time abstains after the draft"; the ordering checks already in
`tests/api/test_ask.py` run on these streams too.

### AC5

**Mark:** HTTP test

**Scenario:** Given the answer model returning claims for the covered part and a `gap` sentence naming the
part the passages don't cover, when the claims are verified, then the stream sends the checked claims and
then the gap sentence as a last `claim` with no sources (shape in "Contract"), and `done {outcome:
answered}`.

**Planned test:** e2e of the API, `tests/api/test_answer.py`, "a partly covered question names the missing
part".

### AC6

**Mark:** HTTP test

**Scenario:** Given `route` classifying a question as `career_fact` (salary, availability, visa, time
zone), when the FAQ has a matching passage, then retrieval reads only FAQ chunks and the answer cites
them. Given no FAQ passage matching, then no answer model is called and the stream ends with
`done {outcome: abstained}`; the front shows the abstention with the contact link (AC7).

**Planned test:** e2e of the API, `tests/api/test_answer.py`, "career facts are answered only from the
FAQ", "a career fact the FAQ lacks abstains without an answer call".

### AC7

**Mark:** Playwright test (zorzo-site)

**Scenario:** Given a recorded answer stream, when the page plays it, then the draft text shows marked
"checking against sources", the checked claims replace it with numbered citations, an `unsupported` claim
is struck through as "removed: not supported by the sources", and clicking a citation opens the passage's
text, its source title and link from `corpus.json`. Given a recorded abstention stream, then the draft is
replaced by "not in my sources" with the passages found and the contact link. Given a recorded decline
stream, then the page shows the decline message. Recorded streams validate against the SSE schema.

**Planned test:** Playwright, `zorzo-site/tests/e2e/answer.spec.ts`, "the draft is replaced by checked
claims", "an unsupported claim is struck through", "a citation opens its passage", "an abstention replaces
the draft", "a decline shows its message".

### AC8

**Mark:** HTTP test

**Scenario:** Given a covered question, a question the corpus doesn't cover (passages but no supported
claim) and an `off_topic` question, then `done.outcome` is `answered`, `abstained` and `declined`
respectively, and the run's result carries the same value from the stored outcomes enum
(`questions_log.outcome`). The `off_topic` question makes no `answer` or `judge` call.

**Planned test:** e2e of the API, `tests/api/test_answer.py`, "abstention and decline are distinct
outcomes".

## Contract

### POST /api/v1/ask · altered

Normal mode now streams the agent's events instead of search-only. Status codes, errors and the request
body don't change.

| Item | Before | After |
| --- | --- | --- |
| Events in normal mode | `stage` (retrieve, rerank), `degraded {reason: models_unavailable}`, `retrieval`, `done` | `stage` (route, retrieve, rerank, assess, reformulate, answer, verify, repair), `retrieval`, `model`, `token`, `claim`, `usage`, `done`; `degraded` + `retrieval` + `done` when the run falls to search-only (reasons `models_unavailable`, `deadline`) |
| `done.abstained` (bool) | Always `false` | Removed |
| `done.outcome` | — | New, required: `answered` \| `abstained` \| `declined` \| `blocked` \| `degraded` \| `error` |
| `claim` for the gap sentence (AC5) | — | New: a last `claim` with `sources: []` and `verdict: "gap"`; the verdict enum gains `gap` |
| `model` | Never sent | One per model call that answered: `stage`, `model`, `provider`; `fallback_reason` stays `null` in this task |
| `usage` | Never sent | Once, before `done`: summed `tokens_in`, `tokens_out`, `cost_usd`; `latency_ms` of the whole request |

The ordering rules of LLD "Stream events" hold unchanged. `contracts/sse-v1.schema.json` is regenerated
from `rag_core.events`, and `zorzo-site`'s copy in `tests/fixtures/` follows.

**Answer contract** (what the `answer` and `repair` stages return; LLD "6.4 Answer contract and
verification"):

```json
{"answer_lang": "en", "claims": [{"text": "…", "sources": [1, 2]}], "gap": "…" | null, "abstained": false}
```

`sources` are passage numbers (1 to the number of passages in the prompt), mapped to chunk ids by the
service. **Judge output:** `{"verdicts": [{"i": 0, "verdict": "supported" | "partial" | "unsupported",
"supported_text": "…" | null}]}`. **Route output:** `{"intent", "standalone_question", "language",
"follow_up", "public_ok"}`, with `intent` in the LLD 6.3 enum and `language` an ISO 639-1 code. **Assess
output:** `{"sufficient": bool}`. **Reformulate output:** `{"query": "…"}`.

**Rule values (`limits.yaml`), new:** `answer_reserve: 12`, `max_reformulate: 1`, `max_repair: 1`,
`recursion_limit: 12`, `context_max_tokens: 2500` (all from LLD section 9); `relevance_floor` and
`assess_skip_score` are optional and absent (see "Decisions").

**Models config:** a provider's `base_url` may contain `${VAR}`, read from the environment at load
(`config/models.local.yaml` uses `${OLLAMA_URL}/v1`).

**Dependency:** `langgraph==1.2.21` (published 2026-11-12; 1.2.22–1.2.24 are under two weeks old).

No table changes.

## Contract verification

- `stage` order on the answer, reformulate and repair paths: AC2.
- `degraded {reason: deadline}` and `{reason: models_unavailable}` from inside the run: AC2.
- `token` before `claim`, `claim` with sources and verdict, `usage`, `done {outcome: answered}`: AC3, AC4.
- `claim` for the gap sentence: AC5.
- `done {outcome: abstained}` and `{outcome: declined}`: AC4, AC6, AC8.
- `model` event: AC4 (validated against the schema; its presence is asserted on the answer path).
- `done {outcome: blocked | degraded | error}`: the existing gate, degraded and error tests of
  `tests/api/test_ask.py` and `tests/api/test_degraded.py`, updated to read `outcome`.
- `${VAR}` in a provider's `base_url`: unit test in `tests/core/test_models_config.py`, "a base URL reads
  its variable from the environment" (not a criterion: configuration).

## Decisions

- **Library before own code.**
  - Graph: LangGraph 1.2.21 (imposed, ADR 0006). Checked in the installed package (spike, 2026-11-27):
    nodes get our services through `Runtime` context (`context_schema`), so no globals; cancelling the
    request's task, or `asyncio.timeout` around `ainvoke`, cancels the running node (its `CancelledError`
    arrives); `recursion_limit` raises `GraphRecursionError` at the limit. No checkpointer in this task
    (F01-T07).
  - Structured output: the gateway already sends the stage's JSON Schema through LiteLLM
    (`response_format`). For reading answers, `langchain_core.utils.json`: `parse_json_markdown` reads
    fenced JSON, and `parse_partial_json` reads the growing JSON of the streamed draft (checked in
    langchain-core 1.8.4, installed). No `instructor` or LangChain output parser: they would wrap the model
    call, which belongs to the gateway (ADR 0016).
  - Verification: DeepEval and RAGAS faithfulness metrics make their own model calls through their own
    clients, outside the gateway, its budget and its deadlines; they stay in the eval harness (F01-T11). The
    citation check and the judge prompt are ours (a set lookup and one batched call).
- **The draft is the claims' text, streamed from JSON.** The answer stage streams the contract JSON; the
  node reads it with `parse_partial_json` as it grows and sends as `token` only the new characters of the
  claims' text (claims joined by a space). The contract stays the LLD's JSON, and the visitor never sees
  JSON or passage numbers. Discarded: a line format with inline `[n]` markers (a second parser, and markers
  in the draft).
- **An unreadable draft is asked again with the schema.** When the streamed JSON doesn't validate, the
  answer stage runs once more through `complete` with the contract as output schema; the gateway already
  gives a schema miss one more try on the same entry and then the next entry, which is LLD 6.4's "one
  retry, then the next model". No more `token` events are sent; the claims replace the draft.
- **Passage numbers, not chunk ids, in the prompt.** The model cites 1..n; the service maps them to chunk
  ids. A number outside 1..n removes the claim (LLD 6.4: an id outside the retrieval set). Short numbers
  are harder to invent than ids.
- **Claims sent are the published answer's.** After a repair, the claims of the repaired answer are sent,
  each with its verdict (its `unsupported` ones too, struck through); the first answer's claims are not
  sent. A `partial` claim is sent with the judge's `supported_text` in place of its text.
- **"Nothing coherent left"** means no claim `supported` or `partial`. Then repair once (the answer stage
  again, with the passages and the judge's verdicts), verify again, and abstain if nothing is left.
- **Failures by moment.** `route`, `assess` or `reformulate` failing on every entry, or less than
  `ANSWER_RESERVE` left before `answer`: search-only (`degraded`), with the passages already found or a
  retrieval of the raw question. `answer` failing after its first token, or the judge failing or running
  out of time: abstention after the draft (a draft is never published unverified). `answer` failing before
  its first token: search-only.
- **Assess and reformulate are bounded to one pass.** `assess` → insufficient → one `reformulate` →
  retrieve and rerank with its query → `answer`, without a second `assess`; the answer may still return
  `abstained: true`. Passages with no hit at all abstain without calling `assess` or `answer`.
- **`RELEVANCE_FLOOR` and `ASSESS_SKIP_SCORE` are off until M3.** They stay optional in `Limits` and absent
  from `limits.yaml`: off means abstain only on zero passages and always run `assess`. A number without a
  calibration would be a guess (LLD 6.3 "calibrated on the eval set at M3").
- **Career facts read the FAQ only.** `career_fact` (salary, availability, visa, time zone) retrieves with a
  `kind = faq` filter; `VectorStore.search` gains a `kinds` filter and `SearchHit` its document's `kind`.
- **Intents in this task.** `about_andre`, `about_system`, `projects_live` and `contact` go through RAG;
  `off_topic` declines with no model answer and `suggestion: null`. F01-T08 adds the guard, the decline
  wording, the suggestion and `contact_options`; F04 adds the tools for `projects_live`.
- **`follow_up` is in the route output now**, so F01-T07 consumes it without changing `route`; this task
  ignores it (no history yet).
- **`done.outcome` replaces `done.abstained`.** One field names the result (`answered`, `abstained`,
  `declined`, `blocked`, `degraded`, `error`), using the stored outcomes' names; `cached` stays a separate
  flag (a cached answer is `answered`). v1 is not frozen and no client reads `abstained` (the front
  ignores it).
- **Context window:** passages enter the prompt whole, in rerank order, up to `CONTEXT_MAX_TOKENS`
  (`CHARS_PER_TOKEN` estimate, the gateway's); the lowest-ranked go first.
- **Prompts live in the domain package** (`domains/zorzo/prompts/*.md`, LLD "Code structure"), loaded at
  startup by `rag_core.domain`; the core only fills slots. Retrieved text is wrapped in delimiters marked as
  data; the full untrusted-content marking is F01-T08.
- **The local profile reads Ollama's address from `OLLAMA_URL`** (`${OLLAMA_URL}/v1` in
  `models.local.yaml`), closing the LLD open question: one variable for embeddings and chat, `localhost`
  from the host and `ollama:11434` inside compose.
- **LangGraph only in `rag_core.agent`**: a new import-linter contract forbids `langgraph` elsewhere.
- **The gap sentence is a last `claim`** with `sources: []` and the verdict `"gap"`, which the front
  renders as a plain note (no citation, no strike). It stays in reading order and in the stored trace with
  the other claims. Discarded: an optional `done.gap` string (a second place for answer text).
- **`MAX_LOOP_MODEL_CALLS` bounds the tool loop only (F04).** "2 beyond `route`" (LLD 6.3) can't hold
  `assess`, `answer`, `judge`, `repair` and a second `judge`; this graph's ceiling is its fixed path: route,
  assess, reformulate (≤ `MAX_REFORMULATE`), answer (plus the gateway's schema retry), judge, repair
  (≤ `MAX_REPAIR`), judge. The LLD 6.3 sentence says so.
- **The recursion backstop abstains and logs `recursion_limit` at `error`** (LLD "Logs" already lists it).
  No alert kind is added (the catalog in LLD 6.10 has none for bugs); flagging the run waits for the stored
  trace (F01-T10).
- **AC8's stored outcome is split with F01-T10**, which writes the questions-log row: this task proves
  `done.outcome` and the run's outcome value (from the stored enum); F01-T10's AC2 proves the row. The AC
  text stays.

## Files and interfaces

- `pyproject.toml`, `packages/core/pyproject.toml`: `langgraph==1.2.21`; import-linter contract for
  `langgraph`.
- `packages/core/src/rag_core/agent/` (new): `state.py` (`AgentState` TypedDict: question, route result,
  passages, draft, answer, verdicts, counters, outcome), `context.py` (`AgentContext`: gateway, embedder,
  store, rerankers, limits, prompts, sink, spend, deadline), `nodes.py` (one async function per step),
  `graph.py` (`build_graph() -> CompiledStateGraph`, `run_agent(question, corpus, trace_id, context) ->
  RunResult` with the outcome and `public_ok`).
- `packages/core/src/rag_core/services/route.py` (new): `RouteResult` model, `route(question, …)`.
- `packages/core/src/rag_core/services/verification.py` (new): `AnswerContract`, `Verdicts`,
  `read_draft(text) -> AnswerContract | None`, `draft_text(partial) -> str`, `check_citations(answer,
  passages)`, `apply_verdicts(...) -> list[CheckedClaim]`, `coherent(claims) -> bool`.
- `packages/core/src/rag_core/services/search_only.py`: `run_search_only` also takes passages already
  found; the stage helper moves to a shared module used by the nodes.
- `packages/core/src/rag_core/services/gateway.py`: `_completion` reads fenced JSON with
  `parse_json_markdown` before validating.
- `packages/core/src/rag_core/events.py`: `Done.outcome`, `Done.abstained` removed; `gap` in the `Claim`
  verdict enum.
- `packages/core/src/rag_core/ports/__init__.py`: `SearchHit.kind`; `VectorStore.search(..., kinds=None)`.
- `packages/core/src/rag_core/adapters/pgvector_store.py`: the `kinds` filter in the hybrid query; `kind`
  in the hits.
- `packages/core/src/rag_core/limits.py`, `config/limits.yaml`: the new values.
- `packages/core/src/rag_core/models_config.py`: `${VAR}` in `base_url`; `config/models.local.yaml`.
- `packages/core/src/rag_core/domain.py`: `load_prompts(name) -> Prompts`.
- `domains/zorzo/src/domain_zorzo/prompts/` (new): `route.md`, `assess.md`, `reformulate.md`,
  `answer.md`, `judge.md`, `repair.md`.
- `apps/api/src/rag_api/ask.py`: normal mode runs `run_agent`; degraded mode keeps `run_search_only`.
- `apps/api/src/rag_api/main.py`: compiles the graph and loads the prompts at startup.
- `contracts/sse-v1.schema.json`: regenerated.
- `tests/fakes/openrouter.py`: replies per stage model for route, assess, reformulate, answer (JSON,
  streamed word by word as now) and judge; `tests/fixtures/corpus/faq-en.md` gains a salary-free
  availability and time-zone passage only if the current one doesn't cover the tests.
- `zorzo-site`: `assets/js/ui/ask.js` (draft, claims, citations, abstention, decline), `assets/js/i18n.js`
  (strings in both languages), `assets/styles.css`, `tests/fixtures/streams/answer.sse`,
  `abstained.sse`, `declined.sse`, `tests/fixtures/sse-v1.schema.json`, `tests/e2e/answer.spec.ts`.

## Existing tests that change

- `tests/api/test_ask.py`: the search-only tests in normal mode ("streams search-only passages", "streams
  no hits when nothing matches", the hung-search and trace-sink tests) move to a degraded setup or script
  the fake for the agent, since normal mode now answers; the rerank tests keep their assertions (the fake's
  default reply is not route JSON, so the run still goes search-only after the rerank).
- `tests/api/test_degraded.py`, `tests/api/test_ask.py`: `done.abstained` → `done.outcome`.
- `tests/contract/` schema drift test: passes again after regenerating `contracts/sse-v1.schema.json`.
- `zorzo-site/tests/e2e/streams.spec.ts` and the recorded streams: `outcome` in `done`.

## Data change

None.

## Updated documents

- `docs/lld.md`:
  - "1. Code structure": Agent row, `langgraph` pinned to 1.2.21 and only in `rag_core.agent`.
  - "Configuration": `${VAR}` in a provider's `base_url`.
  - "Stream events": `done.outcome`; the `gap` claim.
  - "6.3 Agent run": route output fields; career facts from the FAQ; failures by moment; one-pass assess
    and reformulate; floor and skip off until M3; `MAX_LOOP_MODEL_CALLS` bounds the tool step only; the
    backstop abstains and logs.
  - "6.4 Answer contract and verification": passage numbers, `gap`, the streamed draft and its
    schema-bound retry, "nothing coherent", which claims are sent.
  - "8. Client", "Front against the API": citations, the `gap` note, abstention and decline reactions.
  - "9. Values and configuration": `RELEVANCE_FLOOR` / `ASSESS_SKIP_SCORE` off until M3,
    `MAX_LOOP_MODEL_CALLS` scope, `OLLAMA_URL` row.
  - "12. Open questions": the Ollama address question closed.
- `zorzo-site/docs/lld.md`: the answer panel module (draft, claims, citations), in the zorzo-site PR.
