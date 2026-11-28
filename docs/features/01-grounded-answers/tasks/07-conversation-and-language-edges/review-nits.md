# Review nits left for later

Findings the F01-T07 reviews rated as nits (or as test-strength issues), left out of the PR on purpose:
none of them changes behaviour today. A later cleanup task picks them up.

## Conversation store and Postgres

- `packages/core/src/rag_core/adapters/postgres.py` `open_checkpoint_pool`: the statement and lock timeouts
  are built as a libpq `-c` string, while `Database.short_transaction` sets them with `set_config`; share the
  milliseconds conversion of `SHORT_STATEMENT_S`.
- `packages/core/src/rag_core/adapters/postgres.py` `checkpoint_setup_connection`: no statement or lock
  timeout, so a stalled `CREATE INDEX CONCURRENTLY` or a concurrent `CREATE SCHEMA` hangs startup silently.
  Wrap the setup in `asyncio.timeout` with a named constant, and log it.
- `packages/core/src/rag_core/adapters/postgres.py`: the `-c search_path=` startup options are rejected by
  transaction-mode poolers (PgBouncer); if production ever uses one, startup fails loudly.
- `apps/api/src/rag_api/main.py`: `pool.close()` waits its default 5 s on top of `SHUTDOWN_DRAIN_S`, against
  Docker's 10 s; pass an explicit short timeout.
- Every request reads and writes a checkpoint, including `no_context`, decline and block runs, and a fresh
  session id gets a row even when blocked or abstained (bounded by the rate limit). Skip the write when
  `conversation` is unchanged. Purging stale threads is F01-T15 AC4's.
- `packages/core/src/rag_core/agent/graph.py` `run_agent`: branches on `agent.checkpointer` only for the
  tests; always pass a saver (`InMemorySaver` in tests).
- `apps/api/src/rag_api/ask.py` `AskRequest.session: UUID` accepts nil and v1 UUIDs; the nil UUID shares one
  thread among every client that sends it. Type it as `UUID4`.

## Agent run

- `packages/core/src/rag_core/agent/nodes.py`: redaction runs synchronously on the event loop in `route_step`
  and once per claim in `publish_claims` (`ask.py` runs the trace's in a thread). Measure it; redact the
  claims once, joined, or off the loop if it matters.
- `packages/core/src/rag_core/agent/nodes.py` `_conversation(state)`: the stored dict is validated again in
  `route_step` and `verify_step`; parse it once.
- `packages/core/src/rag_core/agent/nodes.py` `publish_claims`: takes `conversation` only to return
  `with_claims`; let `verify_step` build that update.
- `packages/core/src/rag_core/agent/nodes.py` `REMEMBERED_VERDICTS` sits mid-file; move it to the constants
  block (PEP 8 rule).
- `packages/core/src/rag_core/agent/nodes.py` `route_step`: a turn is stored for `public_ok: false` and
  declined questions (`begin_turn` before the action check): noise in the history, no leak.
- `packages/core/src/rag_core/agent/nodes.py` `route_step`: `notice: other_language` is also sent on declined,
  degraded and contact outcomes (consistent with the plan; a note only).
- `packages/core/src/rag_core/agent/state.py` `AgentState`: nothing enforces that every key but
  `conversation` is an `UntrackedValue`; add a test that reads the annotations.
- `packages/core/src/rag_core/services/conversation.py` `route_input` and `begin_turn`: the pop loops
  recompute the size on every iteration; a small trim helper is simpler.
- `packages/core/src/rag_core/services/conversation.py` `size()`: counts the texts' tokens only, not the JSON
  around them, so the input can slightly exceed `HISTORY_MAX_TOKENS`.
- `packages/core/src/rag_core/services/prompts.py` `repair_messages`: builds a throwaway user message from
  `answer_messages`; share a render helper for the system text.

## Tests

- The `SESSION_TTL` boundary is not pinned: add a test at exactly `ttl`.
- No test that a run cut by the deadline keeps its turn, that a failed checkpoint write ends with `error`
  before the claims, or that it keeps the ending already sent after them.
- No test of two runs of one session at once (last write wins, accepted by the plan).

## zorzo-site

- `assets/js/data/api.js` `SESSION_TTL` duplicates `session_ttl` in zorzo-api's `limits.yaml` with no drift
  check (a plan decision); add a comparison test, or at least a comment naming the other copy.
- `assets/js/data/api.js` `load()` prefers the in-memory map over `sessionStorage`: after a failed `setItem`,
  a stale id could win.
- `assets/js/data/api.js` `newConversation`: `store(ASKED_KEY, "")` is a sentinel; use `removeItem`.
- `assets/js/ui/ask.js` `showWithExamples` builds its hint `<p>` by hand instead of `noteLine`.
- `assets/js/ui/ask.js`: the `ansNew` handler repeats the `ansClose` steps; fold both into
  `closePanel({renew})`.
