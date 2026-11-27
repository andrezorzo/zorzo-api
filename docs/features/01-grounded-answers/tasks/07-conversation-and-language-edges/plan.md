# Plan for F01-T07: Conversation and language edges

Task: [task.md](task.md) · Spec: [spec.md](../../spec.md) · PRD: [prd.md](../../prd.md)

The task gives the agent graph a memory per session: the LangGraph Postgres checkpointer keeps the
conversation, `route` reads it to rewrite follow-ups, and a follow-up with nothing before it abstains with a
fixed message. A question in a language the domain doesn't declare already gets an answer in the default
language (F01-T06, F01-T14); this task adds the line that says so. The answer prompt gets the length
targets, and the front gets the "new conversation" button and the idle reset. Guard and suggestions are
F01-T08, the cache F01-T09, the stored trace F01-T10, the multi-turn dataset F01-T11.

## Criteria

### AC1

**Mark:** HTTP test

**Scenario:** Given a visitor whose first question was answered, when they ask a follow-up in the same
session, then `route` receives the first turn (its standalone question and its published claims) in its
input and the follow-up is answered, not treated as a follow-up without context. Given the same follow-up
from another session id, then it is an abstention with `done.notice: no_context` (AC2). Given a new API
instance on the same database (a restart), the follow-up still finds the first turn, and the checkpointer's
`langgraph.checkpoints` table has a thread for the session id. Given a finished run, then no stored blob of
the thread holds a passage or the draft: only the conversation is stored. Given five turns, then `route`
receives the last `HISTORY_TURNS` verbatim, the older standalone questions as a summary within
`HISTORY_SUMMARY_TOKENS`, and the whole history within `HISTORY_MAX_TOKENS`, the oldest dropped first and the
last question always kept.

**Planned test:** e2e of the API, `tests/api/test_conversation.py`, "a follow-up reads the earlier turn",
"another session has no history", "the conversation survives a restart", "only the conversation is
stored"; unit, `tests/core/test_conversation.py`, "history keeps the last turns", "older questions become a
bounded summary", "the route input stays within the history budget".

### AC2

**Mark:** HTTP test; Playwright test (zorzo-site)

**Scenario:** Given a new session, when `route` marks the question as a follow-up, then the stream sends the
`route` stage and ends with `done {outcome: abstained, notice: no_context}`, with no `retrieval` event and no
`answer` or `judge` call, and the turn is not remembered. Given a conversation whose last turn is older than
`SESSION_TTL` (the app's clock moved forward), then a follow-up in that session ends the same way, and a
question that isn't a follow-up starts a new conversation. Given a recorded `no_context` stream, when the
page plays it, then it shows "I don't have earlier context in this conversation — what are you asking
about?" in the interface language, with the example questions, instead of "not in my sources".

**Planned test:** e2e of the API, `tests/api/test_conversation.py`, "a follow-up with no history asks what
the visitor means", "a conversation idle past the session TTL starts over"; Playwright,
`zorzo-site/tests/e2e/conversation.spec.ts`, "a follow-up without context shows its message".

### AC3

**Mark:** HTTP test; Playwright test (zorzo-site)

**Scenario:** Given `route` returning `language: es`, when the question is answered, then the `answer`
request asks for the domain's first language (`en` for zorzo) and the stream ends with `done {outcome:
answered, notice: other_language}`. Given `language: pt`, then the answer is asked in `pt` and `notice` is
null. Given a recorded `other_language` stream, then the page shows one line saying the site answers in
English and Portuguese above the checked claims.

**Planned test:** e2e of the API, `tests/api/test_conversation.py`, "another language is answered in the
default language with a notice", "a declared language has no notice"; Playwright,
`zorzo-site/tests/e2e/conversation.spec.ts`, "an answer in another language shows the language line".

### AC4

**Mark:** unit test

**Scenario:** Given `limits.yaml`, when the answer prompt is filled, then its system message carries
`ANSWER_WORDS_DEFAULT` as the target and `ANSWER_WORDS_MAX` as the ceiling for a request for more detail,
and the repair prompt, which embeds it, carries both too. Given an `answer.md` without `$words_default` or
`$words_max`, the API doesn't start. Given `config/models.yaml` and `config/models.local.yaml`, the `answer`
stage's `max_tokens` leaves room for `ANSWER_WORDS_MAX` words (two tokens a word, which covers Portuguese and
the contract's JSON).

**Planned test:** unit, `tests/core/test_prompts.py` (new), "the answer prompt carries the length targets",
"an answer prompt without a length slot stops the app"; `tests/core/test_models_config.py`, "the answer
stage has room for the longest answer".

### AC5

**Mark:** Playwright test (zorzo-site)

**Scenario:** Given a page with a conversation, when the visitor clicks "new conversation", then the answer
panel clears and the next question is sent with a different session id. Given a question sent, when the
page's clock moves past `SESSION_TTL` and the visitor asks again, then the request carries a new session id
and the page shows the "new conversation" notice; a question before `SESSION_TTL` keeps the session id, and a
reload keeps it too. The API side (a new session id has no history) is AC1.

**Planned test:** Playwright, `zorzo-site/tests/e2e/conversation.spec.ts`, "the new conversation button
starts a new session", "an idle conversation starts over with a notice".

## Contract

### POST /api/v1/ask · altered

The request body doesn't change; `session` now keys the conversation. Status codes and errors don't change.

| Item | Before | After |
| --- | --- | --- |
| `done.notice` | — | New, optional, default null: `no_context` \| `other_language` \| null; one of the optional fields `done` gains in F01 (with `contact` and `suggestion` from F01-T08 and `cached` from F01-T09) |
| Follow-up with no history | Ran as any question | `stage` (route), `usage`, `done {outcome: abstained, notice: no_context}`; no `retrieval`, no `token`, no `claim` |

The ordering rules of LLD "Stream events" hold unchanged. `contracts/sse-v1.schema.json` is regenerated from
`rag_core.events`, and `zorzo-site`'s copy in `tests/fixtures/` follows.

**Route input** (the `route` stage's user message; LLD "6.3 Agent run"): JSON instead of the bare question.

```json
{"question": "and what was the result?",
 "history": [{"question": "What did André build at Acme?", "answer": "He built … He led …"}],
 "earlier": ["Which AWS services has André used?"]}
```

`history` is the last `HISTORY_TURNS` turns, oldest first, each answer the published claims' text joined by a
space; `earlier` is the summary, oldest first. With no conversation both are empty. The route output doesn't
change.

**Stored conversation** (the only persisted value of the graph's state, as plain types):
`{"turns": [{"question": str, "claims": [str]}], "earlier": [str], "updated_at": float}`, `updated_at` in
seconds since the epoch (UTC).

**Answer prompt slots:** `$words_default` and `$words_max` join `$language` and `$passages`, and all four are
required.

**Rule values (`limits.yaml`), new:** `history_turns: 2`, `history_summary_tokens: 250`,
`history_max_tokens: 800`, `session_ttl: 1800` (seconds), `answer_words_default: 150`,
`answer_words_max: 400` (all from LLD section 9).

**Models config:** the `answer` stage's `max_tokens` goes from 700 to 1000 in `config/models.yaml` and
`config/models.local.yaml`.

**Dependencies:** `langgraph-checkpoint-postgres==3.2.1` (published 2026-11-14, two weeks old on
2026-11-28) and `psycopg-pool` (3.3.3 in the lock).

### langgraph schema · zorzo · new (owned by the library)

- Tables `checkpoint_migrations` (`v` primary key), `checkpoints` (primary key `thread_id`, `checkpoint_ns`,
  `checkpoint_id`; `checkpoint` and `metadata` JSONB), `checkpoint_blobs` (primary key `thread_id`,
  `checkpoint_ns`, `channel`, `version`) and `checkpoint_writes` (primary key `thread_id`, `checkpoint_ns`,
  `checkpoint_id`, `task_id`, `idx`), with an index on `thread_id` each. Their columns are the `MIGRATIONS`
  list of the pinned `langgraph.checkpoint.postgres.base`, run by `setup()` at the API's startup; no Django
  migration and no reflection. `thread_id` is the session UUID as text.
- The schema itself is created by the API at startup when missing. The backup already leaves it out (LLD
  "7. Background processes"), and the retention job (F01-T15) deletes idle threads.

## Contract verification

- `done.notice: no_context`, with no `retrieval` and no answer call: AC2.
- `done.notice: other_language`, and null for a declared language: AC3.
- The route input carries the history within its limits: AC1.
- The thread keyed by the session id, kept across a restart, holding only the conversation: AC1.
- A conversation idle past `SESSION_TTL` read as empty: AC2.
- Required answer prompt slots and the answer stage's `max_tokens`: AC4.
- The `langgraph` tables' shape is the library's and has no criterion of its own: AC1 reads
  `checkpoints` and `checkpoint_blobs` through it.

## Decisions

- **Library before own code.**
  - Conversation store: `langgraph-checkpoint-postgres` (imposed by the LLD, ADR 0001), its
    `AsyncPostgresSaver` over a `psycopg_pool.AsyncConnectionPool`. Checked in the downloaded 3.2.0 and 3.2.1
    wheels: `setup()` runs its own versioned migrations (`checkpoint_migrations`), one of them `CREATE INDEX
    CONCURRENTLY`, so the connections need `autocommit=True`, plus `row_factory=dict_row` and
    `prepare_threshold=0`, as its own `from_conn_string` sets; `adelete_thread` and `aprune` exist; there is no
    TTL in the checkpointer (the Postgres *store* has one, the saver doesn't). 3.2.0 serializes every call of
    a saver through one `asyncio.Lock` even over a pool; 3.2.1 drops the lock for a pool, so 3.2.1 is pinned
    (two weeks old on 2026-11-28, before implementation starts; 3.2.0 would also work at this traffic).
    `ShallowPostgresSaver` (one checkpoint per thread) is deprecated in the package.
  - Per-run values: LangGraph's own `UntrackedValue` channel ("stores the last value received, never
    checkpointed"). Checked with a spike against the installed langgraph 1.2.21 and `InMemorySaver`: keys
    annotated `Annotated[T, UntrackedValue]` are visible to every node and in `stream_mode="values"`, are
    absent from the saved state and start empty in the next run of the thread; `durability="exit"` writes
    one checkpoint per run, and a run that raises after a node wrote the conversation still saves it.
  - Summary of older turns: LangChain's `trim_messages` and summarization middleware work on message lists
    and the latter makes a model call outside the gateway; the history here is two turns plus a list of
    questions, trimmed with the gateway's `estimate_tokens`. No library.
  - Session ids and the idle reset in the front: `crypto.randomUUID()` and `sessionStorage`, as today.
- **The whole graph is checkpointed, and only the conversation persists.** `build_graph(checkpointer)`
  compiles with the saver; every per-run key of `AgentState` (question, route result, passages, answer,
  claims, counters, outcome, degraded, notice) is an `UntrackedValue`, and one plain key, `conversation`,
  is stored. Runs use `configurable.thread_id = str(session)` and `durability="exit"`. The edges keep
  reading `"outcome" in state`: an untracked key starts absent in each run. Discarded: a separate
  conversation graph or direct `aput` calls (a second state machine, or the saver's internal checkpoint
  format), and resetting stored per-run keys at each input (every edge would have to read values, and the
  passages and claims would be written to Postgres on every run).
- **No new graph node.** `route_step` reads the conversation and writes the new turn's question;
  `verify_step` writes the published claims' text when it sends them. A run cut by the deadline keeps what it
  wrote (exit durability saves on an exception). Discarded: a `finish` node every exit goes through (it
  would rewrite every edge, and a deadline cut skips it); see "Cross-task decisions".
- **Only `route` reads the history.** The standalone question carries the context to retrieval, the answer
  and the judge, so their prompts and the verification stay as they are. The history goes in `route`'s user
  message as JSON strings, like the claims of the judge (LLD 6.4 "Untrusted text in a prompt"), and
  `route.md` keeps `$intents` as its only slot.
- **What a turn keeps.** The standalone question (it reads on its own, unlike the raw follow-up) and the
  published claims' text (`supported`, and `partial` with its supported part); not `unsupported` claims nor
  the gap sentence. An abstention or decline after `route` keeps the question with no claims; a search-only
  end after `route` too, and so does F01-T08's contact reply, which leaves after `route`. A `no_context`
  follow-up, a run whose `route` failed, a degraded-mode question and a blocked question are not remembered:
  the guard runs from `START`, before `route`, and a block ends the run there, so a stopped injection is never
  replayed to `route` on later turns.
- **Turns are stored redacted.** The question and the claims of a turn go through F01-T10's shared redactor
  before they are written (it detects no names). F01-T10 merges before this task, so the redactor exists when
  this task is built; if it doesn't, turns are stored as they are until it does, and the backup leaves the
  schema out.
- **The summary of older turns is their standalone questions, with no model call.** When a turn falls out of
  the last `HISTORY_TURNS`, its standalone question joins `earlier`, and the oldest questions go first past
  `HISTORY_SUMMARY_TOKENS` (the gateway's `estimate_tokens`). Discarded: a `summary` field in the route output,
  rewritten by `route` each turn (one more field in a shared output, and a model can drop facts); a
  summarization call (one more model call per turn, outside LLD 6.3's bounded path).
- **The idle limit is checked in the API too.** `route_step` reads a conversation whose `updated_at` is
  older than `SESSION_TTL` as empty, so a client that keeps an old id still starts over; the front's reset
  (AC5) stays the visible one. `AgentContext` gains `now` (wall clock at the request's start, from
  `app.state.clock`, which tests move forward). Deleting idle threads is the retention job's (LLD "6.11
  Retention"), added as F01-T15 for this; until it runs, idle threads stay in `langgraph`, unread.
- **The stored state is read with a strict serializer.** The saver gets `JsonPlusSerializer(
  allowed_msgpack_modules=None)`: only plain types come back from a blob. LangGraph's default rebuilds any
  importable class named in a stored value; the conversation is plain dicts, lists, strings and a float,
  validated by a Pydantic `Conversation` model on read.
- **The pool lives in the Postgres adapter; the saver in `rag_core.agent`.** `open_checkpoint_pool(url)`
  in `adapters/postgres.py` opens the connections (keepalives, `POOL_WAIT`, `search_path=langgraph`, and
  `statement_timeout`/`lock_timeout` at `SHORT_STATEMENT_TIMEOUT` on request-time connections; `setup()` runs
  on its own connection without them); `rag_core.agent.memory` builds the saver from it. Both import
  contracts hold with no exception, and `psycopg_pool` joins the network modules only adapters may import.
  The saver needs a running loop, so the graph is compiled in the lifespan, after `reflect`.
- **A failing checkpointer is a failing database.** Loading the thread fails before any event, and the run ends with `error` then `done`, as for any database failure in the run; a failed write at the
  end, after the claims, leaves the ending already sent (`context.settled`). Two runs of one session at once
  (a new question while the old stream is still cancelling) keep the last write; a lost turn costs one
  follow-up's context.
- **Edge questions reach the front as a notice code.** `route_step` ends a follow-up with no live history as
  `abstained` with `notice = "no_context"`, before retrieval; it sets `notice = "other_language"` when
  `route.language` isn't one of the domain's languages, and the run continues (the answer language already
  falls back to the first). `run_agent` puts `notice` in `done`; `no_context` wins when both apply. The front
  owns the texts, in the interface language, as it owns "not in my sources". `notice` is optional with a
  null default, like the other fields `done` gains in F01. Discarded: the API sending the text from the
  domain package (a new per-language messages file, and the API would write interface text the front already
  writes for every other outcome); a `claim` with verdict `gap` (the gap means "not in the sources", and the
  abstention's "not in my sources" would show too).
- **Length is the prompt's, bounded by `max_tokens`.** The answer prompt gets `$words_default` and
  `$words_max` and tells the model to aim at the first and to go up to the second only when the question asks
  for more detail; `route.md` tells the router to keep such a request in the standalone question. No route
  field for "detail" (it would change the route output) and no cut of a verified answer (claims are checked
  as a set; a cut could drop the gap sentence). The answer stage's `max_tokens` goes to 1000: 400 words take
  about 650 tokens in Portuguese, plus the claims' JSON. `load_prompts` checks the answer prompt's four slots
  as it checks `route.md`'s.
- **No "open the sources" sentence past `ANSWER_WORDS_MAX`.** The clause "beyond that the answer suggests
  opening the sources" leaves LLD 6.3: such a sentence would carry no citation, and verification strikes an
  uncited claim; every claim's citation already opens its source. The PRD's S2 line is the owner's to
  revisit. Discarded: a fixed front line under every answer (noise on short answers), the model writing it in
  `gap` (the gap means "not in the sources").
- **AC3 names the domain's first language** (owner-approved change of the AC text, 2026-11-27): the task said
  "answered in English with one line saying the site answers in both"; it now follows LLD 6.3 and the
  core-as-a-base rule of F01-T14. With zorzo the test is the same.
- **The checkpointer's migrations run at API startup.** `CREATE SCHEMA IF NOT EXISTS langgraph` and `setup()`
  in the lifespan, after `reflect` and before serving; a failure stops the API like a missing table. The HLD's
  "the API has no migrations" is about the Django schema; the library's `setup()` is idempotent and
  versioned. Discarded: a Django migration (the dashboard would depend on `langgraph`, against ADR 0015, or
  copy the library's SQL); a separate deploy command (one more step to forget, with no deploy feature yet).
- **First question in degraded mode.** The degraded path asks whether the conversation is live, through
  `rag_core.agent.memory.load_conversation(agent, session, now, limits)`, outside the graph, and passes
  `first_question=not live` to F01-T09's `run_degraded`, which passes `True` until this wiring exists. The
  wiring is this task's; with the merge order below, if `run_degraded` is not on `main` when this task is
  built, the one-line call goes in F01-T09's PR, with this helper.

**Cross-task decisions** (agreed with F01-T08 to F01-T11, planned in parallel)

- **Merge order:** F01-T10 → F01-T08 → F01-T07 → F01-T09 → F01-T11. This task builds on the guard node and the
  shared redactor, and F01-T09 builds on the conversation.
- **`done` fields:** `done` gains optional fields only: `contact` and `suggestion` (F01-T08), `notice` (this
  task), `cached` (F01-T09). Whether `suggestion` is set on a `no_context` abstention is F01-T08's; the front
  shows the example questions with it either way.
- **Graph order:** guard (from `START`) → route → cache → retrieve. The contact reply and a block leave before
  `cache`; a blocked question never enters the history. Every per-run state key is an `UntrackedValue`, so
  only `conversation` persists; a key another task adds follows the same rule unless it is meant to persist.
  A cache hit (F01-T09) remembers its claims like verified ones, through `conversation.with_claims`.
- **Redactor:** F01-T10 owns the shared redactor (it detects no names); the stored turn goes through it.
- **`model_config_version`** (F01-T09 owns it) hashes the prompts, `route` as rendered, and the intents,
  without the canary line; this task's edits to `route.md` and `answer.md` and its six new limits change it.
- **Stored outcomes:** the `error` outcome is stored (F01-T10); a run ending in `error` sends no `notice`.
- **Multi-turn eval:** F01-T11's `multi turn` dataset runs after this task merges, turns through a session
  thread (this saver, or LangGraph's `InMemorySaver`); whether the harness measures answer length (AC4's
  "aim") is F01-T11's.
- **LLD sections shared with those plans:** "6.3 Agent run", "Stream events" (`done` row), "8. Client"
  (reactions table), "9. Values and configuration". The edits here are scoped to their own paragraphs and
  rows.

## Files and interfaces

- `packages/core/pyproject.toml`, `uv.lock`: `langgraph-checkpoint-postgres==3.2.1`, `psycopg-pool>=3.2`.
- `pyproject.toml`: `psycopg_pool` in the network modules of the import contract, allowed in
  `rag_core.adapters`.
- `packages/core/src/rag_core/adapters/postgres.py`: `open_checkpoint_pool(url) -> AsyncConnectionPool`;
  `create_checkpoint_schema(pool)`.
- `packages/core/src/rag_core/agent/memory.py` (new): `checkpointer(pool) -> AsyncPostgresSaver` (strict
  serializer), `setup(saver)`, `load_conversation(agent, session, now, limits) -> Conversation`.
- `packages/core/src/rag_core/services/conversation.py` (new, no I/O): `Turn`, `Conversation` (Pydantic, dumped
  to plain types), `live(conversation, now, ttl) -> Conversation`, `begin_turn(conversation, question, now,
  limits) -> Conversation`, `with_claims(conversation, claims) -> Conversation`, `route_input(question,
  conversation, limits) -> str`.
- `packages/core/src/rag_core/agent/state.py`: per-run keys `Annotated[…, UntrackedValue]`; `conversation:
  dict[str, Any]`; `notice`.
- `packages/core/src/rag_core/agent/context.py`: `AgentContext.now: float`.
- `packages/core/src/rag_core/agent/graph.py`: `build_graph(checkpointer: BaseCheckpointSaver | None = None)`;
  `run_agent(question, session, trace_id, *, agent, context)` with the thread config and `durability="exit"`
  when a checkpointer is set; `Done.notice` from the state.
- `packages/core/src/rag_core/agent/nodes.py`: `route_step` (live conversation, `no_context`,
  `other_language`, `begin_turn`, through F01-T10's redactor); `verify_step` (`with_claims` on a published
  answer); `_answer_messages` fills the word targets.
- `packages/core/src/rag_core/services/route.py`: `route(question, conversation, …)`, the user message from
  `route_input`.
- `packages/core/src/rag_core/domain.py`: `load_prompts` requires the answer prompt's four slots.
- `packages/core/src/rag_core/events.py`: `Done.notice: Literal["no_context", "other_language"] | None`.
- `packages/core/src/rag_core/limits.py`, `config/limits.yaml`: the six values.
- `config/models.yaml`, `config/models.local.yaml`: `answer.max_tokens: 1000`.
- `domains/zorzo/src/domain_zorzo/prompts/route.md` (the JSON input, `follow_up` with and without history,
  keeping a request for detail), `answer.md` (`$words_default`, `$words_max`);
  `tests/fixtures/domains/domain_sample/prompts/route.md` and `answer.md` likewise.
- `apps/api/src/rag_api/main.py`: the pool, the schema, `setup()` and `build_graph(saver)` in the lifespan;
  the pool closes at shutdown; `app.state.clock = time.time`.
- `apps/api/src/rag_api/ask.py`: `run_agent` gets `body.session`; `AgentContext.now` from the clock;
  `first_question` for F01-T09's `run_degraded` from `load_conversation`.
- `contracts/sse-v1.schema.json`: regenerated.
- `tests/api/test_conversation.py`, `tests/core/test_conversation.py`, `tests/core/test_prompts.py` (new);
  `tests/fakes/openrouter.py` unchanged (route replies are scripted per request already).
- `zorzo-site`: `assets/js/data/api.js` (`newConversation()`, last-question time in `sessionStorage`, idle
  reset), `assets/js/ui/ask.js` (button, notice, `done.notice` lines), `assets/js/i18n.js` (the strings in
  both languages), `assets/js/config.js` or the existing config (`SESSION_TTL`), `tests/fixtures/streams/
  no-context.sse`, `other-language.sse`, `tests/fixtures/sse-v1.schema.json`,
  `tests/e2e/conversation.spec.ts`; `docs/lld.md` (the conversation id section).

## Existing tests that change

- `tests/core/test_route.py`: the user message is the route input JSON (`"question"` plus empty history),
  not the bare question.
- `tests/core/test_graph.py`: `run_agent` takes the session; the looping test graph has no checkpointer.
- `tests/api/test_answer.py`, `tests/api/test_ask.py` and the other e2e tests: `QUESTION` shares one session,
  so later questions now carry history to `route`; the scripted route replies keep `follow_up: false`, so
  outcomes don't change, and tests that need a clean conversation use a fresh session id.
- `tests/core/test_intents.py`, `tests/core/test_domain.py`: fixture prompt packages get the answer slots.
- `tests/contract/` schema drift test: passes after regenerating the schema.
- `zorzo-site/tests/e2e/streams.spec.ts` and the recorded streams: `notice` in `done`.

## Data change

None. The `langgraph` schema starts empty; no existing row changes.

## Updated documents

- `docs/lld.md`:
  - "1. Code structure", Conversation state row: `AsyncPostgresSaver` pinned, over the adapter's pool, strict
    serializer; only the conversation stored.
  - "4. Data", conventions: the API creates the `langgraph` schema and runs the library's migrations at
    startup; not reflected, not in Django.
  - "Stream events", `done` row: `notice`.
  - "6.3 Agent run": route's JSON input; new "Conversation" item (what is stored, the summary, the history
    budget, the idle limit, which turns are remembered, redaction, concurrency); "Edge questions" with the
    notices; "Length" with the prompt slots and `max_tokens`, without the "suggests opening the sources"
    clause.
  - "6.4 Answer contract and verification", untrusted text: the conversation in `route`'s user message.
  - "6.11 Retention": idle by the latest checkpoint's time.
  - "8. Client": the button clears the panel; idle counted from the last question; the API's own idle
    check; reactions to `done.notice`.
  - "9. Values and configuration": where `HISTORY_*` and `SESSION_TTL` apply.
- `zorzo-site/docs/lld.md`: the idle reset and the button, in the zorzo-site PR.
- `docs/features/01-grounded-answers/tasks/07-conversation-and-language-edges/task.md`: AC3's text (decision
  above).
- `docs/features/01-grounded-answers/tasks/15-retention-job/task.md` (new): the retention job of LLD "6.11
  Retention", including the `langgraph` threads, approved by the owner with this plan.
