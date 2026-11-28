# Review nits left for later

Findings the F01-T08 reviews rated as nits (or as test-strength issues), left out of the PR on purpose:
none of them changes behaviour today. A later cleanup task picks them up. Accepted security gaps are not
here; they are in `link-and-canary-residual-risk.md`.

## Agent run and guard

- `packages/core/src/rag_core/agent/nodes.py` `_block`: `settled = "blocked"` is set before the defense row is
  written and `Blocked` is sent; if `defenses.record` ever raised, `done{blocked}` would go out with no
  `blocked` event. Send `Blocked` first, or guard the record call.
- `packages/core/src/rag_core/agent/nodes.py` `answer_step`: the sentinel is tested with
  `isinstance(answer, str)`; `answer == LEAKED` says what it means. `publish_claims` is only used in the
  module (`_publish_claims`), and its docstring's "the one way" is not enforced.
- `packages/core/src/rag_core/agent/nodes.py` `_stream_draft`: a canary found in a middle chunk ends the
  stream before any `Completion`, so that call's tokens and cost never reach the trace. Record the gateway's
  reserved estimate on abort, or accept it.
- `packages/core/src/rag_core/agent/nodes.py` `_send_draft`: streamed `token` text has the canary check and the
  hold-back but no `links_allowed`; a bad link in a draft is shown until verification replaces it. The page
  shows text, never a link.
- Consider one `ctx.block(layer, category, message)` helper and a per-outcome table next to `NO_USAGE_OUTCOMES`
  (usage sent, suggestion offered) instead of `("abstained", "declined")` in `graph.py` and `settled` set at
  each early end.
- `packages/core/src/rag_core/services/retrieval.py`: `vectors: MutableMapping` is threaded through
  `retrieve` → `search` to keep one embedding per run; a `ctx.query_vector` (or `search` returning the
  vector) is simpler. Left for F01-T09, which reuses the vector.
- `packages/core/src/rag_core/agent/graph.py` `_suggestion`: a declined run has no retrieval vector, so the
  suggestion pays one embed round-trip after the outcome is decided; it could start at route time.
- `packages/core/src/rag_core/services/guard.py` `check_input`: a `-inf` score passes like any score; a
  probability outside [0, 1] could only come from a broken classifier, so it could be an error.
- `packages/core/src/rag_core/adapters/prompt_guard.py` `softmax_malicious`: `reshape(-1)` doesn't check that
  exactly two logits remain (a batch or another export shape gives a wrong probability); very large finite
  logits raise numpy's overflow warning (`np.errstate(over="ignore")`); `other` can be inlined.
- `packages/core/src/rag_core/services/guard.py` `_squash_char`: `@cache` is unbounded; `lru_cache(maxsize=…)`.
- `packages/core/src/rag_core/services/guard.py` `_LINK_FRAGMENT`: broader than the link matcher needs
  (`data:` with no media type, `www` with no dot, `http:` with no destination), so a claim with an undoubled
  JSON backslash and prose like "Key data: …" is refused. Require each branch's continuation.
- `packages/core/src/rag_core/services/guard.py`: the comment above `_LINK_FRAGMENT` states the argument
  backwards. Removing a control character does join fragments; what holds is that drawing one as a gap or a
  glyph only splits text, so the fully joined text holds every fragment any drawing shows.
- `apps/api/src/rag_api/settings.py`: the canary format is checked by two regexes plus a recount, and
  `CanaryWatch.__init__` raises again on an empty squashed canary, which settings already rule out.
- `apps/api/src/rag_api/main.py` startup: when `classifier.warm` raises, `asyncio.gather` doesn't cancel the
  other warm-ups, which keep running past `db.close`. Use a TaskGroup.
- `packages/core/src/rag_core/services/gate.py`: a 429 or a failed bot check queues two background inserts
  (event log and defense log, 1000 pending each); the comment "no trace id, since a refused request has no
  trace" sits next to a row that carries one; ruff left a trailing comment on a closing paren (lines 75–77,
  and `ask.py` 176–178).
- `domains/zorzo/src/domain_zorzo/domain.yaml`: the comment says a claim with a link outside the allowlist "is
  removed"; it is sent `unsupported`.

## Tests

- `tests/core/test_guard.py`: `test_a_control_character_stays_a_link_boundary` and
  `test_a_control_character_inside_a_link_hides_nothing` now pass through the control-character refusal
  before the link regex runs; merge them into one test. `test_a_control_character_beside_an_allowed_link_is_refused`
  also asserts that tab and newline pass; split it.
- No test drives `cite`, `apply_verdicts` and `enforce_links` with a control character per verdict
  (supported, partial kept text, unsupported, gap).

## Records

- `link-and-canary-residual-risk.md`: name the exact fragment list (`//`, `www`, `javascript:`, `vbscript:`,
  `data:`, `http(s):`) and say format characters are removed before the fragment search too.

## zorzo-site (the AC5 part)

- `assets/js/ui/ask.js` `showAbstention` / `showDecline`: they still append `contactNote()` while `render`
  appends the `.ans-contact` list, so the same contact link can show twice.
- `assets/js/ui/ask.js` `showBlocked`: "A mistake? Get in touch" links to `#contact`, not to the
  `done.contact` options the LLD reactions table names.
- `assets/js/ui/ask.js` `suggestionNote`: one suggestion is introduced with the plural "Try one of these:";
  add a singular i18n key.
- `assets/js/ui/ask.js` `render`: the drafted-answer-with-no-claims branch writes `t().error` by hand; going
  through `showFailure` would give it the warn style, the trace link and no contact links, like every other
  failure.
- `assets/js/i18n.js`: the block-message lookup is a plain object (`{…}[category] ?? fallback`), so a
  category like `constructor` would print inherited text (rendered with `textContent`, so cosmetic). Use a
  `Map` or `Object.hasOwn`.
- `index.html`: "Built with Llama" bypasses the bilingual rule; if the license wants the exact English phrase,
  say so in a comment or the site LLD.
- `tests/e2e/fixtures.ts`: the comment on hand-written streams doesn't list `blocked.sse`,
  `blocked-output.sse` and `contact.sse`.
- `tests/e2e/guard.spec.ts`: no test for the contact links after a degraded reply with no hits, nor for no
  `.ans-contact` on a failure. The offline-suggestion test swaps the health route while the first check may
  still be queued, and proves "no request" with a fixed 300 ms wait; asserting `#q` is unchanged is
  synchronous.
- `tests/e2e/health.spec.ts` ("goes offline and recovers by itself", "shows the degraded notice from health")
  fails now and then under the full parallel suite, on `main` too (seen 2 of 3 full runs on 2026-11-28).
