# 06: Verified answers with citations

**Status:** in progress

**What to build:** A visitor asks about André's career, projects or how the site was built and gets a draft marked as being checked, then the checked answer with a numbered citation on every claim; unsupported claims show as removed, and questions the corpus doesn't cover get an abstention.

**Decisions owned:** the `route` output fields; the stream semantics of abstention and decline.

**Decisions consumed from other tasks:** The SSE schema and TraceSink (02); the model config (05).

**Blocked by:** 03 Web foundation; 05 Model gateway and budget.

- [ ] **AC1** · `route` returns, in one structured-output call, the intent, the standalone question, the language and `public_ok` (LLD "6.3 Agent run").
- [ ] **AC2** · The bounded graph runs route, retrieve, rerank, assess, optional reformulate, answer, verify and optional repair, within the limits and deadlines of LLD "6.3 Agent run" (ADR 0006).
- [ ] **AC3** · The answer contract, per-claim citation check, batched judge, repair once and abstention follow LLD "6.4 Answer contract and verification"; verification is never skipped.
- [ ] **AC4** · The stream sends `token` for the draft, then `claim` per checked sentence with sources and verdict, `usage` and `done`; if verification can't finish in time, the answer becomes an abstention with sources.
- [ ] **AC5** · A partly covered question is answered for the covered part with one sentence saying which part is not in the sources.
- [ ] **AC6** · Salary, availability, visa and time zone are answered only from the FAQ; otherwise an abstention pointing to contact.
- [ ] **AC7** · The page shows the draft marked "checking against sources", replaces it with checked claims, strikes through unsupported ones and opens a citation's passage, title and link on click.
- [ ] **AC8** · Abstention and decline are distinct outcomes in `done` and in the stored outcome.
