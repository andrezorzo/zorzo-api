# Visual check (example questions and health notices)

The page served by `tools/serve.py` from `zorzo-site` (branch of F01-T03) on 2026-11-25, headless Chromium
with software WebGL (SwiftShader). The plan asked for screenshots of the new example list at 1440 and 390
for review ("Decisions", "Example questions").

Local setup: the published data is the demo copy of the prototype's corpus (127 chunks); `/api/*` goes to a
local API over the fixture database (fake embedder), or to nothing for the offline shots. The two corpora
differ, so the passages in the answer shots are demo chunks that happen to share the API's hit ids: they
show the layout, not a real match.

| File | What it shows |
| --- | --- |
| `screenshots/examples-1440.webp`, `screenshots/examples-390.webp` | Online: the example questions under the box (EN at 1440, PT at 390); the box and the intro sit above them |
| `screenshots/offline-1440.webp`, `screenshots/offline-390.webp` | API unreachable: box disabled, the offline notice, no examples |
| `screenshots/answer-1440.webp`, `screenshots/answer-390.webp` | After clicking an example: steps panel, passages, the example list hidden while the answer is open |

## Result

- The example list sits under the box, in the interface language, and is hidden while an answer is open;
  its height stays reserved, so the box doesn't move when the answer opens.
- On the phone, long questions end with an ellipsis on one line each, so five examples fit above the
  credit line.
- The offline notice replaces the examples under the disabled box; the search-only notice (health
  `mode: degraded`) shows in the same place, above the examples.
- In portrait, the steps panel sits above the box and the examples, as before plus the list's height.
