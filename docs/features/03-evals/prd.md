# PRD — F03 Evals

2026-11-24 · André de Castro Zorzo · Status: approved

Feature 3 of 4 of the MVP (see the [roadmap](../../roadmap.md)). Builds on
[F01](../01-grounded-answers/prd.md) and [F02](../02-transparency/prd.md). Product context: [product brief](../../product-brief.md), sections "Overview" (promises "Grounded" and
"The controls are the demo") and "MVP scope" (item 5). If the launch date is at risk, this page is the
second cut; the evals themselves keep running and gating changes.

## 1. Executive summary

- **Problem:** "my AI answers are accurate" is another unverifiable claim; evaluators can't see whether
  quality is measured, how, or what happens when it drops.
- **Proposed solution:** a public page shows the eval results of the live system: quality per layer and
  per language, the trend across versions, the questions that failed and why, and the datasets behind it.
- **Success criteria** (product brief, "Goals and success metrics"): Answer quality; Inbound opportunities.

## 2. User experience and behavior

### Personas

- **Technical evaluator:** staff engineer or tech lead judging whether André measures AI quality the way a
  production team should.
- **Evaluator:** recruiter or engineering manager who wants a plain answer to "is it accurate?".
- **Secondary:** developers studying how to evaluate a RAG system.

### User stories and acceptance criteria

**S1. See current quality.** As an evaluator, I want the latest quality numbers of the live system, so that
I can judge whether its answers can be trusted.

- The page shows the latest results for the version visitors are using now, with the date of the run.
- Results are shown for each metric of the eval strategy (F01): faithfulness, answer relevancy, context
  relevancy, correctness, citation validity, abstention, decline, language match and public-view judgment,
  each with its target and whether it passes.
- Each metric has one plain sentence a non-engineer understands (for example, "faithfulness: share of
  claims backed by the cited sources") and a link to the details.
- English and Portuguese results are shown separately.

**S2. See where a problem comes from.** As a technical evaluator, I want results split by layer
(retrieval, generation, end to end), so that I can see whether a drop comes from search or from the model.

- Each layer's metrics are shown on their own, with the same version and date.

**S3. See the trend across versions.** As a technical evaluator, I want to see quality across corpus and
model versions, so that I can see changes are measured and regressions caught.

- A trend shows each metric across versions, marking which versions passed the gate and went live.
- A version that failed the gate is shown as blocked, not hidden.

**S4. See what failed.** As a technical evaluator, I want to see the questions that failed in the latest
run and the ones whose result changed between versions, so that I can see the process catches errors.

- Each failed question shows the question, the expected result, the answer given and why it failed.
- Exception: a failed question about a fact of André's career shows only the question and why it failed,
  never the wrong answer.
- Questions whose result changed between the last two versions (passed → failed and failed → passed) are
  listed, with both results side by side, under the same exception.

**S5. Know what runs where.** As a technical evaluator, I want to know which results come from the
pre-release runs and which from the live free models, so that I can read the numbers correctly.

- Results are split into "pre-release (PR and baseline)" and "live (nightly, free models)".
- One sentence explains why: pre-release runs use the paid endpoints of the same models; the nightly run
  measures what visitors actually get, and the two can differ.
- A live result worse than its target is shown as such, with the date it was detected.

**S6. Inspect the datasets.** As a developer, I want links to the eval datasets, including the attack
set, so that I can see what is tested and reuse the approach.

- The page links to every dataset in the public repository and says what each one covers and how many
  questions it has.
- It states that real visitor questions enter the datasets only anonymized and after André's review.

**S7. Bilingual page.** As a visitor, I want the page in English or Portuguese, following my choice on the
site.

### Non-goals

- Running evals on demand from the page.
- Online judging of a sample of live answers (costs budget shared with visitors); later phase.
- Comparison of models, providers and rerankers (Phase 2 in the brief).
- The blocked attempts page (F04), though attack-set results appear here as eval results.
- Visitor feedback (thumbs up or down) on answers.

## 3. AI system requirements

### What the agent can do

- This PRD adds no agent behavior. It publishes how the agent of F01 and F02 is measured.
- The judge that scores answers is a model too: it is never changed in the same release as the answering
  model, and a judge change is validated against a sample labeled by André before it is used.

### Evaluation strategy

- **Page consistency:** the numbers on the page match the stored results of the run they cite (checked
  automatically after each run).
- **Exception check:** no failed career-fact question on the page shows the answer given.
- **Freshness:** the live results are never older than two nights without a notice on the page.

## 4. Product risks

| Risk | Mitigation |
| --- | --- |
| A wrong answer about André becomes a showcase | Failed career-fact questions show only the question and the reason (S4 exception); everything shown was caught before reaching a visitor |
| Bad numbers on a public page look worse than no page | Showing failures caught is the promise; targets, pass or fail and trend give context; if the date is at risk this page is cut before the controls |
| Numbers are misread (pre-release vs live) | Results split and explained (S5) |
| Live results go stale or silently drift | Nightly run with an alert to André on regression; freshness notice on the page |
| Datasets reveal how the defenses work | The defenses don't rely on secrecy; the public repository already shows them |
| The page is too technical for recruiters | One plain sentence per metric; a clear pass or fail next to each number |

## Open questions

None at this time.

