# PRD — F04 Agent and defenses

2026-11-24 · André de Castro Zorzo · Status: approved

Feature 4 of 4 of the MVP (see the [roadmap](../../roadmap.md)). Builds on
[F01](../01-grounded-answers/prd.md), [F02](../02-transparency/prd.md) and [F03](../03-evals/prd.md). Product context: [product brief](../../product-brief.md), sections "Overview" (promise "The
controls are the demo"), "MVP scope" (items 1 and 5) and "Risks, dependencies and assumptions".

If the launch date is at risk, this PRD holds two cuts of the agreed order: the blocked attempts page goes
first, the agent tools go third. The defenses themselves are in F01 and are never cut; without the tools,
direct answers with sources stay.

## 1. Executive summary

- **Problem:** a corpus-only answerer can't say what André is working on now or open a specific document,
  and "my AI is safe" is another unverifiable claim unless attacks can be seen being stopped.
- **Proposed solution:** the agent gets a few read-only tools (open a document, list projects, recent
  GitHub activity of his public repos, contact options), and a public page shows the attacks blocked by
  each defense, with numbers, examples and the rate of legitimate questions wrongly blocked.
- **Success criteria** (product brief, "Goals and success metrics"): Answer quality; Inbound opportunities.

## 2. User experience and behavior

### Personas

- **Evaluator:** recruiter or engineering manager who wants to know what André is working on now.
- **Technical evaluator:** staff engineer or tech lead probing whether the agent and its defenses are
  production grade, sometimes by attacking it.
- **Client:** company or founder checking his recent and public work.
- **Secondary:** developers studying how to defend an LLM application.

### User stories and acceptance criteria

**S1. Ask what André is working on now.** As an evaluator, I want to ask about his recent work and get
current facts, so that I see more than a static résumé.

- Questions about recent activity are answered with live data from his public GitHub repositories on an
  allowlist: the last few commit titles and dates, open issues count, last release.
- The data comes from GitHub's public metrics, never from reading repository content, and is kept short
  so it adds few tokens to the answer; it is fetched only when the question needs it and reused for a
  few minutes.
- Live data is cited as a source with the time it was fetched (for example, "GitHub, fetched at 14:32").
- This is the only exception to "answers come only from the corpus": no other live data, no web search,
  no private repositories.
- When GitHub is unavailable, the answer says so and falls back to the corpus.

**S2. Ask about projects and documents.** As a client, I want to ask for his projects or a specific
document and get a precise answer, so that I can go straight to the work.

- "What projects has he built?" returns the public projects with links and status.
- A question about one document (for example, the résumé or a design decision) can be answered from that
  document as a whole, with a summary and a link.

**S3. Contact options.** As a client, I want to ask how to reach André and get his contact options, so that
I can reach out without searching.

- The agent returns the contact links; it never sends anything on the visitor's behalf.

**S4. Watch the agent use its tools.** As a technical evaluator, I want to see which tool the agent used and
why, so that I can judge its reasoning.

- Each tool use appears as a step while the answer is produced (F01, S3) and in the trace (F02, S1),
  with the tool's name and a short reason in the visitor's language.
- The agent uses a few tools per question at most; when it reaches the limit, it answers with what it has
  or says "not in my sources".

**S5. Know why I was blocked.** As a visitor, I want to see which defense stopped my request and why, so
that a block is understandable and not a silent failure.

- A blocked request shows the defense layer and the reason in plain words (for example, "blocked by the
  input guard: looks like a prompt injection"), with a link to the blocked attempts page.
- The message reveals nothing the public repository doesn't already show.
- A "this was a mistake?" link leads to the contact options.

**S6. See attacks being stopped.** As a technical evaluator, I want a public page with the attacks blocked
by each defense, so that I can see the controls working, with numbers.

- The page shows blocks per defense layer and per category for the last 7 and 30 days.
- It explains each layer in one plain sentence (for example, input checks, injection classifier, untrusted
  content marking, read-only tools, output checks, grounding).
- Examples come only from the attack dataset, reviewed by André; attacks from real visitors appear only as
  counts.
- It shows the rate of legitimate but suspicious-looking questions wrongly blocked, measured on the
  corresponding eval dataset, next to the block counts.

**S7. Bilingual.** As a visitor, I want tool steps, block messages and the page in English or Portuguese,
following my choice on the site.

### Non-goals

- Tools that act: sending email, booking calls, writing anywhere, running code, fetching arbitrary URLs.
- Web search, or live data beyond the allowlisted public GitHub repositories.
- Career graph tools for precise counting and timeline answers (Phase 2).
- Real visitor attack text on the public page.
- A way to appeal a block other than contacting André.
- A free-form agent loop; the agent follows fixed steps and uses tools only within them.

## 3. AI system requirements

### What the agent can do

- Read-only tools: search the corpus, open one indexed document, list public projects, read recent
  activity of allowlisted public GitHub repositories, return contact options.
- Tool arguments are restricted to known values (indexed documents, allowlisted repositories); tool
  results are treated as untrusted data, like sources, and instructions inside them are never followed.
- Never: acts on the visitor's behalf, fetches arbitrary URLs, runs code, reads private repositories,
  calls more tools than the per-question limit, or reveals its hidden instructions.

### Evaluation strategy

- **Tool use:** eval questions carry the tools they require and the tools allowed; the agent's path is
  scored (right tool used, no forbidden tool, within the limit). Target: TBD.
- **Attack set:** direct and indirect injection, jailbreaks, prompt extraction, defamation bait and
  off-topic requests, each expected to be blocked or declined at a specific layer. Target: TBD.
- **False blocks:** legitimate questions that look suspicious must be answered, not blocked. The rate is
  shown on the page. Target: TBD.
- **Faults:** tool timeouts, model errors, empty results and GitHub down; expected: recover (fallback,
  retry or "not in my sources") without loops or hangs. Target: 100% recovered.
- **Leak check:** no response or trace contains the hidden instructions (a planted marker is checked on
  every answer). Target: zero leaks.

## 4. Product risks

| Risk | Mitigation |
| --- | --- |
| A tool injects wrong or manipulated data into an answer | Tool results treated as untrusted; arguments restricted to known values; answer checked against sources before shown |
| Live GitHub data embarrasses (a stale or broken repository) | Allowlist chosen by André; data cited with fetch time |
| Defenses block legitimate recruiters | False-block rate measured and shown; "this was a mistake?" link to contact |
| The blocked attempts page shows something offensive | Examples only from the reviewed attack dataset; real attacks only as counts |
| Showing the defenses helps attackers | The defenses don't rely on secrecy; the repository is public anyway |
| Tools make answers slower | Few tools per question; tool steps shown live; 3 s first-words target from F01 still applies |
| Not enough time for tools before launch | Cut order: this page first, tools third; direct answers with sources stay |

## Open questions

- [ ] Targets for tool use, attack-set blocking and false-block rate (set with the eval harness).

