# Review nits left for later

Findings the F01-T09 reviews rated as nits, left out of the PR on purpose: none of them changes behaviour
today. A later cleanup task picks them up.

## Answer cache and language detection

- `packages/core/src/rag_core/adapters/language.py:15`: the comment says a broken model stops the API at
  startup; `main.py` `_warm_language_detector` only logs, and each lookup is then a miss. Fix the comment.
- `packages/core/pyproject.toml`: `fast-langdetect==1.2.0` is listed twice. Keep one.
- `fast-langdetect` pulls `robust-downloader`, `requests`, `tqdm` and `colorlog`, unused on the lite path (more
  supply-chain surface than the feature needs). Consider vendoring the model or a lighter detector later.
- Concurrent identical questions each store a row (no unique key), so hits split between the rows until they
  expire. Add one line about it to LLD "6.9 Answer cache", or add a key.
- `packages/core/src/rag_core/agent/state.py`: `query_vector`'s formatting is split by the trailing comment.
  Move the comment above the field.
- `packages/core/src/rag_core/agent/state.py` / `context.py`: add a one-line comment that the `query_vector`
  state key flags "the lookup ran and missed", unlike `ctx.vectors`, which holds every embedded text.
- `packages/core/src/rag_core/agent/graph.py` `_remember`: the guard list is redundant (`cached` is covered by
  `vector is None`; the two `outcome` checks could be one early return).
- `packages/core/src/rag_core/agent/graph.py` `_remember`: it rebuilds `Claim` from the verified claims; reuse
  the constructor `verify` uses (`_send_claims`).
- `packages/core/src/rag_core/adapters/answer_cache.py` `_claims` coerces a malformed row to `[]` and the
  service's `_read` validates again; validate in one place.
- `packages/core/src/rag_core/adapters/answer_cache.py` `lookup`: `SET LOCAL hnsw.iterative_scan` is an extra
  round trip per lookup; fold it into a `set_config` call in the same statement, or set it per connection.
- `packages/core/src/rag_core/agent/nodes.py` `cache_step`: the lookup runs strictly before retrieval on a
  miss; the two could overlap (optional).
- `packages/core/src/rag_core/adapters/language.py`: the fastText `detect` is sync on the event loop; use
  `anyio.to_thread` if questions get long.

## zorzo-site

- `assets/js/ui/ask.js`: `done?.cached` is read twice in `render`; hoist a `const`.
