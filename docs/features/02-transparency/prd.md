# PRD — F02 Transparency

2026-11-24 · André de Castro Zorzo · Status: approved

Feature 2 of 4 of the MVP (see the [roadmap](../../roadmap.md)). Builds on
[F01](../01-grounded-answers/prd.md). Product context: [product brief](../../product-brief.md), sections "Overview" (promise "Transparent") and "MVP scope"
(items 4 and 5).

## 1. Executive summary

- **Problem:** a cited answer is still a claim; evaluators can't see how it was produced, what it cost, or
  whether the system is healthy, so "trust me" remains the message.
- **Proposed solution:** every answer links to a public trace of how it was produced, and public pages show
  the system's activity, status, budget and the list of sources the agent knows.
- **Success criteria** (product brief, "Goals and success metrics"): Trace open rate; Inbound opportunities.

## 2. User experience and behavior

### Personas

- **Evaluator:** recruiter or engineering manager checking André for a remote senior role.
- **Technical evaluator:** staff engineer or tech lead who wants to see how an answer was produced.
- **Client:** company or founder looking for a freelance or consulting engineer.
- **Owner:** André, who keeps the public pages free of embarrassing content.
- **Secondary:** developers studying the system.

### User stories and acceptance criteria

**S1. Open the trace of an answer.** As a technical evaluator, I want to open the trace of any answer, so
that I can see exactly how it was produced.

- Every answer, including "not in my sources", declines and degraded-mode replies, links to its trace.
- The trace shows: the question (with personal data removed), the steps with their time, the sources
  retrieved with their scores, the model and provider of each step and any fallback, each claim of the
  answer with its check result, and the total time and cost.
- Nothing in a trace reveals personal data of the visitor, secrets or hidden instructions.

**S2. Share a trace.** As an evaluator, I want to share the link to a trace with a colleague, so that we can
discuss the same answer.

- Anyone with the link can open the trace; links can't be guessed.
- A trace is available for 30 days; after that its link shows "this trace expired" instead of an error.

**S3. See the system's activity and status.** As an evaluator, I want a public page with how the system is
used and whether it's healthy, so that I can see it's real and running in production.

- The page shows: questions per day, answer outcomes (answered, not in my sources, declined, degraded),
  response time (median and p95) and time per step, most cited sources, topics asked about, and a list of
  recent questions, each linking to its trace.
- The recent questions list shows only questions that were answered or got "not in my sources" and that
  were judged fit for public view (S6); declined, blocked and off-topic questions never appear in it.
- It shows the current status: uptime, which model providers are available, and whether the site is in
  degraded mode.

**S4. See the budget.** As an evaluator, I want to see today's spending against the daily cap and the cost
of each answer, so that I can see cost is controlled, with numbers.

- The page shows today's spending in US dollars against the daily budget cap, and recent days.
- Each trace shows its own cost in US dollars, including zero for free models and cached answers.

**S5. See what the agent knows.** As a visitor, I want the list of sources the agent answers from, so that I
know what it can and can't answer.

- Each source shows its title, kind and date.
- A source whose content is public links to it (for example, the résumé PDF or a document on GitHub); the
  FAQ and career narrative can be read in full on the site.
- The list matches what the agent uses now; a source removed from the corpus disappears from the list.

**S6. Hide offensive questions automatically.** As the owner, I want questions that aren't fit for public
view hidden automatically, so that something offensive a visitor wrote never shows on my site.

- When the question is understood, it is also judged fit or not for public view by content category
  (offensive, harassment, sexual content, attacks on other people, personal data), not by sentiment: a
  critical but legitimate question ("why did that project fail?") stays fit.
- The visitor still gets the answer and its trace; in the trace, a question not fit for public view shows
  as "[hidden: not shown in public]".
- The question is kept privately (personal data removed) so the owner can review wrong judgments; it is
  deleted by the normal retention.
- Judging adds no extra model call and no extra cost.

**S6b. Remove something by hand.** As the owner, I want to take a question or trace off the public pages
in one action, so that anything the automatic judgment misses doesn't stay on my site.

- The owner can remove a trace from public view in one action, without a deploy.
- After removal, the question disappears from the recent questions list and the trace link shows that it
  was removed; the change is visible within a minute.
- Removals are recorded (who, when, which trace) and are not shown in public.

**S7. Bilingual pages.** As a visitor, I want the trace and the public pages in English or Portuguese, so
that I can read them in my language.

- Interface text of the trace and public pages is available in both languages, following the visitor's
  choice on the site.

### Non-goals

- The evals page (F03) and the blocked attempts page (F04).
- The event log page with solved cases (Phase 2).
- Access to the full internal trace (the private tracing tool); the public trace is the redacted version.
- Filters, search or export on the public pages.
- Visitor analytics beyond what the brief's metrics need; no profile of who asked what.
- Editing a trace or an answer; the owner can only remove.

## 3. AI system requirements

### What the agent can do

- Judges whether each question is fit for public view, by content category, in the same step that
  understands the question (S6). It never refuses to answer because of this judgment; it only hides the
  question from public pages.
- Otherwise this PRD adds no agent behavior: it makes visible what the agent of F01 already does.
- The public trace and the live steps of F01 come from the same record, so what the visitor watched is
  what the trace shows.

### Evaluation strategy

- **Redaction check:** the eval set's prompt-injection and personal-data cases (F01) also verify that the
  resulting traces contain no personal data, secrets or hidden instructions. Target: zero leaks.
- **Public-view check:** eval cases with offensive questions and with critical but legitimate ones.
  Targets: 100% of offensive questions hidden; at most 10% of legitimate ones hidden (a guess until
  measured).
- **Consistency check:** for every eval answer, the sources cited in the answer appear among the sources
  retrieved in its trace, and the trace's cost matches the recorded spending.

## 4. Product risks

| Risk | Mitigation |
| --- | --- |
| A visitor writes something offensive that shows in the recent questions list | Only answered, fit-for-public questions are listed; automatic judgment by content category (S6); the owner removes anything missed in one action (S6b) |
| The judgment hides good questions | Judged by category, not sentiment; wrong judgments reviewed privately; false-hide rate measured |
| A trace leaks personal data, a secret or the hidden instructions | Redaction before storage; redaction checked by the eval set; full internal trace only in the private tool |
| Nobody opens the trace, so the transparency promise is untested | Trace link next to every answer; Trace open rate measured against its target |
| The pages look empty before there's traffic | The owner's own questions before launch fill them; numbers are shown honestly from day one |
| Showing cost and outages looks bad | That's the point of the promise: controls shown working, with numbers; degraded mode explained, not hidden |

## Open questions

None at this time.

