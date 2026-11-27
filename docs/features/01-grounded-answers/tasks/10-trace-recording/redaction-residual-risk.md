# Redaction: residual risk (F01-T10)

What the trace redactor (`rag_core/services/redact.py`) is known not to catch, or catches at a cost, after the
review rounds. Each was weighed and accepted; the design is in the module's docstring.

- Cards and CPFs split by a wrapped line, emphasis marks or a tag (`4111 **1111** 1111 1111`) are not found.
  Phones are, because their detector reads the text with markup and wraps removed; the card and CPF rules are
  plain patterns over the text as written, and widening them to read through markup would make ordinary
  numbers in prose look like cards.
- A phone the matcher reads only after its preparation, and that can't be found again in the text as written,
  makes the whole string `[phone]`. This is fail-closed on purpose; it costs the string's other content, and
  the case is rare.
- The cost of phone detection grows with the number of phone-like runs in one string (the matcher is run on
  windows of each run), so a string with thousands of them is slow. Strings here are a question of at most
  `MAX_QUESTION_CHARS`, a few claims and titles, so the bound is small.
- A text that still decodes after `MAX_ENTITY_LAYERS` (3) is replaced whole by `[redacted]`. Nobody writes
  that many layers; the cost is one string.
- Entities are decoded in the stored output (`&amp;` is stored as `&`): the redactor works on one canonical
  text, which is what a page shows. The trace page renders the stored text as text, never as HTML, so nothing
  that was escaped becomes markup there.
- A key split by `-` or `_` into short mixed groups, mostly pure-letter or pure-digit ones, reads as a slug
  (`python3`, `v2` are the reason mixed groups are allowed). A key whose groups are all mixed is caught. A
  key arranged to be a minority of mixed groups is not.
- A name written in a question is not detected (a plan decision, HLD "Security and privacy"): such a trace is
  kept hidden from public pages and deleted with its retention.
- The LangSmith run tree is built after the request, from the moments each event arrived. A step cut by a
  deadline or a failure ends at the time the sink finished, so its duration in LangSmith can be longer than
  it was. The stored trace has the real latency; only LangSmith's view stretches.
- A secret shorter than `MIN_SECRET_CHARS` (4) is not matched exactly, because it would cut its characters out
  of everything. Settings hold none that short.
- The redactor does not read the token's neighbours: a secret written with a character inserted inside it
  (`s e c r e t`) is not found. Only hidden characters, NFKC differences and entities are undone.
