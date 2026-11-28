# Review nits left for later

Findings the F01-T10 reviews rated as nits (or as test-strength issues) and left unfixed: none of them
changes behaviour today. A later cleanup task picks them up. The redactor's accepted coverage gaps are not
here; they are in `redaction-residual-risk.md`.

## Code

- `apps/api/src/rag_api/main.py` `_close_traces`: when `SHUTDOWN_DRAIN_S <= CLOSE_MIN_S`, the trace writes
  get `drain(0.0)` and are cancelled at once, even one about to commit. Not reachable with today's values
  (6 s and 1.5 s); a test that patches `SHUTDOWN_DRAIN_S` to 0.5 runs it. Give the writes a small minimum
  (`CANCEL_WAIT_S`) or document it.
- `packages/core/src/rag_core/adapters/event_log.py` (now `adapters/row_writer.py`): a flood drops event-log
  rows, 429s and abuse rows included, with one warning and a count. Exempting high-severity rows or keeping a
  drop counter metric would keep them visible.
- `packages/core/src/rag_core/adapters/langsmith_tracer.py`: the LangSmith SDK's own logger may log the body
  of an HTTP error response; the run content is already redacted and the key is a header, so low risk.
- `packages/core/src/rag_core/events.py` / the frozen plan: the plan still lists `budget` and `request_cap` as
  `fallback_reason` values; the contract and the LLD list the four the gateway emits.

## Tests

- `tests/core/test_langsmith_tracer.py` `test_a_timeout_under_the_minimum_is_raised_to_it`:
  `pytest.approx(CLOSE_MIN_S, abs=CLIENT_CLOSE_FLOOR_S / 2)` includes 1.0, so a `close` that gives the
  client only its floor after raising a short timeout still passes. Tighten to `abs=CLIENT_CLOSE_FLOOR_S / 4`
  or add `spy.timeout > CLIENT_CLOSE_FLOOR_S`.
- `tests/core/test_langsmith_tracer.py` (stuck-post test): the comment gives the wrong reason for `approx`
  (the two clock reads are at least the wait apart; the real reason is float rounding of the deadline), and
  the line above still says "exactly its floor".
