# Spec — F01 Grounded answers

2026-11-24 · André de Castro Zorzo · PRD: [prd.md](prd.md)

How F01 is built. It follows the [HLD](../../hld.md), the [LLD](../../lld.md) and the ADRs, and points to
them instead of repeating rules and values.

## Problem

Recruiters, engineering managers and clients can't verify a senior engineer's claims from a résumé or a
LinkedIn profile. They want to question his work and check where each answer comes from, quickly, in their
own language, without the site guessing or going dark when the AI is unavailable.

## Solution

A question box on the site sends the visitor's question to the API, which answers from the reviewed corpus
through a bounded agent. The visitor watches the steps live, reads a draft marked "checking against
sources", and then sees the checked answer with a citation on every claim. When the corpus doesn't cover
the question the site abstains; when the question is out of scope it declines; when a defense stops the
request it says which one. Follow-ups use the conversation's context. When the models are down or the
budget cap is reached, the site switches to degraded mode and shows matching passages instead of a written
answer.

## User stories

1. As an evaluator, I want to type a question about André's career or projects, so that I can check his
   claims instead of trusting them.
2. As an evaluator, I want to click one of 4–6 example questions in my interface language, so that I can
   start without thinking of a question.
3. As an evaluator, I want to see the first words of a draft within 3 seconds, so that I know the site is
   working.
4. As an evaluator, I want the draft clearly marked as being checked, so that I don't take it as final.
5. As an evaluator, I want checked claims to replace the draft and unsupported ones to be shown as removed,
   so that I can see the verification working.
6. As an evaluator, I want a numbered citation on every claim, so that I can see which source backs it.
7. As an evaluator, I want to click a citation and read the passage, the source's title and its link, so
   that I can check the claim myself.
8. As an evaluator, I want an answer of about 150 words by default, so that I can read it quickly.
9. As an evaluator, I want a longer answer (up to about 400 words) when I ask for more detail, so that I can
   dig in.
10. As a technical evaluator, I want to ask how the site was built and get answers from its design
    documents, decision records and code map, so that I can judge the engineering behind it.
11. As an evaluator, I want to see each step of the answer start and end, in my language, so that I know it
    isn't a canned reply.
12. As an evaluator, I want an honest "not in my sources" when the corpus doesn't cover my question, so that
    I can trust what the site does say.
13. As an evaluator, I want the answer to cover what the corpus supports and say which part of my question
    it can't answer, so that a partly covered question still helps me.
14. As an evaluator, I want salary, availability, visa and time-zone questions answered only from the FAQ,
    and pointed to André otherwise, so that I never get a guess on those.
15. As a visitor, I want off-topic or out-of-bounds requests declined politely with a suggested question, so
    that I know what I can ask.
16. As a visitor, I want a closest example question suggested with every abstention and decline, so that I
    have a next step.
17. As an evaluator, I want to ask a follow-up that uses my earlier questions, so that I don't repeat
    context.
18. As an evaluator, I want a clear message when I ask a follow-up and there is no earlier context, so that
    I know to rephrase.
19. As an evaluator, I want my conversation to survive a page reload, so that I don't lose context by
    accident.
20. As an evaluator, I want to start a new conversation at any time, so that a new topic isn't mixed with
    the old one.
21. As an evaluator, I want to be told when my conversation started over after a long pause, so that I know
    earlier context is gone.
22. As a visitor, I want the answer in the language of my question, so that I can use my own.
23. As a visitor writing in another language, I want an answer in English with a note, so that I still get
    help.
24. As an evaluator, I want a reused answer labeled as reused, so that I know it wasn't generated live.
25. As a visitor, I want matching passages with a notice when the AI is down or the budget cap is reached,
    so that a visit is never a dead end.
26. As a visitor, I want a clear message when nothing matches in degraded mode, so that I know what to do
    next.
27. As a visitor, I want the page to say when the backend is unreachable and recover by itself, so that I
    don't think the site is broken.
28. As a visitor, I want to see which defense stopped my request and why, so that a block isn't a silent
    failure.
29. As a visitor, I want a clear message saying when I can ask again if I send too many questions, so that
    I know what happened.
30. As a client, I want contact links (LinkedIn, GitHub, email) after every answer, abstention, decline and
    degraded reply, so that I can reach André while convinced.
31. As a visitor on a phone or a device without 3D graphics, I want the full question-and-answer
    experience, so that the site works for me too.
32. As André, I want no change to ship without passing the eval set, so that a regression never reaches a
    visitor.
33. As André, I want the corpus to accept only documents I reviewed and that pass the secret and PII scans,
    so that nothing private is ever answered.

## Implementation decisions

**Scope of the build**

- F01 delivers the answer path end to end: corpus ingest, the API with gate and stream, the bounded agent
  without tools, verification, conversation state, answer cache, degraded mode, the Django schema owner,
  the eval harness with the F01 datasets, and the front's question-and-answer experience in `zorzo-site`.
- Code layout, piece types and allowed dependencies follow LLD "Code structure" and "Components and
  dependencies" (ADR 0012): a shared `core` with ports and adapters, `apps/api`, `apps/ingest`,
  `apps/dashboard` (schema only in F01), and `domains/zorzo` loaded by `DOMAIN` (ADR 0011).

**Modules built**

- **Gate** (core service, called by the HTTP route before the stream opens): Turnstile check, input
  normalization, rate limit buckets, budget state. Failures are plain HTTP errors (LLD "Errors"); rate
  limit rules in LLD "6.7 Rate limit"; budget states in LLD "6.2 Daily budget".
- **Agent graph** (core): a bounded LangGraph state machine (ADR 0006) with the steps guard → route →
  cache lookup → retrieve → rerank → assess → optional reformulate → answer → verify → optional repair →
  finish, plus abstain and decline exits. No tool loop in F01: the `contact` intent returns the contact
  options from the domain config directly; the tool registry comes with F04. Limits, deadlines, retries and
  evidence thresholds per LLD "6.3 Agent run".
- **Guard** (core service): input checks, untrusted-content marking of chunks, output checks (canary,
  scope, rules about a real person, link allowlist) per HLD "Cross-cutting concepts" and LLD "6.4 Answer
  contract and verification". The input injection classifier (layer 2) is chosen in its task: LLD open
  question "L2 guard classifier".
- **Route**: one structured-output call returns the intent, the standalone question, the language and
  `public_ok`; it also detects a follow-up with no history and a language other than English or Portuguese
  (LLD "6.3 Agent run", "Edge questions").
- **Retrieval** (core service): hybrid vector + full-text search fused by RRF in one query over the active
  corpus version (ADR 0001, VectorStore port), then the rerank chain (ADR 0009).
- **Verification** (core service): answer contract parsing, per-claim citation check against this
  request's retrieval set, batched judge call, repair once, then abstain (LLD "6.4 Answer contract and
  verification"). Verification is never skipped.
- **Model gateway** (core service + adapters): per-stage chains across free tiers and the capped paid entry
  (ADR 0007), breakers (LLD "6.6 Circuit breaker"), reserve-then-settle budget (LLD "Concurrency and
  idempotency"), free quota handling (LLD "6.8 Free quota").
- **Answer cache**: Postgres with pgvector (ADR 0014); lookup, hit, storage and degraded-mode rules per LLD
  "6.9 Answer cache".
- **Conversation state**: the LangGraph Postgres checkpointer keyed by the session id; history limits per
  LLD "Values and configuration" (`HISTORY_*`); idle threads removed per LLD "6.11 Retention".
- **Suggestions**: the domain's example questions (4–6 per language, reviewed by André) are embedded at
  startup; on an abstention or decline the closest one by local embedding similarity is returned in
  `done.suggestion`, with no model call.
- **Telemetry**: one callback handler emits the step events that feed the SSE stream, the stored trace and
  LangSmith (LLD "Tracing" row); at the end of a request the trace, spans, questions-log row and final cost
  are written in one transaction (LLD "Transactions"). The trace pages themselves are F02.
- **Alerts raised by the API**: `budget_exhausted` and `providers_down` through the Alerter port (LLD "6.10
  Alerts").
- **Ingest** (`apps/ingest`): Markdown loaders and splitters, `reviewed_by_owner` and gitleaks/PII gate,
  embedding with the pinned Ollama tag, candidate version, eval run, activation (LLD "6.1 Corpus version",
  ADR 0008, ADR 0013).
- **Schema** (`apps/dashboard`, Django migrations): the tables F01 writes, per LLD "Data": corpus
  versions, documents, chunks, answer cache, public traces, trace spans, questions log, defense events, log
  events, rate buckets, budget daily, alert states, eval runs and eval results. The API reflects them with
  SQLAlchemy Core.
- **Eval harness** (core + `domains/zorzo/evals`): F01 datasets (golden, unanswerable, adversarial,
  benign tricky, multi turn, facts) and the metrics and targets of the PRD's evaluation strategy; a passing
  run is required to activate a corpus version.
- **Front** (`zorzo-site`): question box, example questions, live steps, draft and claim rendering,
  citations, suggestions, contact links, conversation id in the tab's session storage with the "new
  conversation" button and idle reset, health states (online, degraded, offline), and every reaction in LLD
  "Front against the API". Without WebGL or with reduced motion the brain becomes a still image (to be
  recorded in `zorzo-site`'s LLD).

**API contract**

- `POST /api/v1/ask` and `GET /api/healthz`, inputs, outputs and error catalog per LLD "HTTP routes" and
  "Errors".
- Stream events and ordering rules per LLD "Stream events"; F01 uses `stage`, `guard`, `retrieval`, `model`,
  `token`, `claim`, `usage`, `blocked`, `degraded`, `error`, `done` and `ping`.
- The contract is published as `contracts/sse-v1.schema.json` (JSON Schema) and an OpenAPI snapshot; the
  front tests against them (HLD "API style").

**Interactions worth stating**

- Draft and claims: tokens stream as the draft; after the first `claim` no more tokens arrive; a claim with
  verdict `unsupported` is rendered struck through. If verification can't finish in time, the draft is
  replaced by an abstention with the sources found.
- Degraded mode: the `degraded` event sends its `retrieval` hits first, then `done`; the front shows up to
  `DEGRADED_MAX_PASSAGES` passages or the "no matching sources" message.
- A cached answer sends `stage`, the cached `claim` events, `usage` with no answer or judge cost (0 unless
  `route` ran on the paid entry) and `done {cached: true}`, with no `token`.

## Testing decisions

- **What a good test is:** it drives the system from the outside, the way a visitor or the front does, and
  asserts on what comes out (HTTP status, stream events, stored rows that other features read). It never
  asserts on internal calls, prompts or graph node order beyond what the stream exposes.
- **Seam 1, the main one: `POST /api/v1/ask` read as a stream.** The real API against a per-session test
  database with the Django migrations applied, the fake OpenRouter server answering scripted responses,
  429s, hangs, malformed JSON and canary leaks (LLD "Development environment"), Turnstile's test keys, and a
  deterministic fake embedder behind the Embedder port. A small fixture corpus is ingested by the real
  ingest command at session start. It covers: gate refusals; answer with citations; abstention; decline;
  block; partly covered question; follow-up and follow-up without context; other language; cached answer;
  degraded mode with a first-question cache hit and with no matches; draft-to-claim verification; the
  suggestion; the ordering rules of LLD "Stream events"; and every event validated against the SSE schema.
- **Seam 2: the browser, in `zorzo-site`.** Playwright tests of the page against recorded SSE streams,
  with no backend. Recorded streams are validated against the same SSE schema. They cover: draft marking
  and struck claims, clickable citations, "new conversation" button and notice, examples in the interface
  language, degraded notice, offline state, and the page without WebGL.
- **Fakes:** the embedder is fake in tests (retrieval tested as a mechanism); retrieval quality and answer
  quality belong to the eval set, with real models, outside the test suite.
- **Modules tested:** through seam 1, the gate, graph, guard, retrieval, verification, cache, conversation
  state, telemetry writes and ingest; through seam 2, the front.
- **Prior art:** none in this repository yet; these become the reference tests.

## Out of scope

- Trace, status, budget and sources pages (F02); evals page (F03); agent tools beyond the direct contact
  options and the blocked attempts page (F04).
- Production infrastructure and deploy (Terraform, CloudFront, VPS bootstrap).
- Event log page and career graph (Phase 2).
- Memory across conversations, accounts, web search, actions on the visitor's behalf.

## Notes

- The real corpus (FAQ with explicit career numbers, career narrative, résumé in Markdown, site documents
  including `docs/code-map.md`) is written and reviewed by André; tests use the fixture corpus.
- Open questions that tasks will settle: the embedding model and vector size, the L2 guard classifier, the
  free models per stage (LLD "Open questions"), and the local language detector library.
- Production infrastructure and deploy have no feature in the roadmap yet; they are needed before the
  launch on 2026-12-04.
