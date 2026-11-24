# 10: Trace recording

**Status:** to do

**What to build:** Every reply leaves a stored record of how it was produced, ready for the trace pages of F02: steps, models, sources, claims, time and cost.

**Decisions consumed from other tasks:** The TraceSink port and step events (02); the outcome values (01).

**Blocked by:** 06 Verified answers with citations.

- [ ] **AC1** · One callback handler feeds the stream, the stored trace and LangSmith from the same step events; LangSmith is on only with a key and never blocks.
- [ ] **AC2** · At the end of a request the public trace, its spans, the questions-log row and the final cost are written in one transaction, never during the stream (LLD "Transactions").
- [ ] **AC3** · The stored trace is redacted: no visitor personal data, secrets or hidden instructions.
- [ ] **AC4** · The `trace_id` in `done` points to a trace that exists once the stream ends.
