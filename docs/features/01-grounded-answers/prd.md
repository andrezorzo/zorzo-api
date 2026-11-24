# PRD — F01 Grounded answers

2026-11-24 · André de Castro Zorzo · Status: approved

Feature 1 of 4 of the MVP (see the [roadmap](../../roadmap.md)). The only one
required for launch. Product context: [product brief](../../product-brief.md), sections "MVP scope" and "Goals and success metrics".

## 1. Executive summary

- **Problem:** hiring managers, recruiters and clients can't verify a senior engineer's claims from a résumé
  or LinkedIn; "built RAG systems" costs nothing to write.
- **Proposed solution:** visitors ask questions about André's career, projects and how this site was built,
  in English or Portuguese, and get a streamed answer grounded in the reviewed corpus, citing its sources,
  with a clear "not in my sources" when the corpus doesn't cover it.
- **Success criteria** (product brief, "Goals and success metrics"): Ask rate, Follow-up rate, Answer quality
  (faithfulness), Inbound opportunities (through the contact links).

## 2. User experience and behavior

### Personas

- **Evaluator:** recruiter or engineering manager at a US or European company checking André for a remote
  senior backend / applied AI role.
- **Technical evaluator:** staff engineer or tech lead running the technical evaluation, who asks how the
  site itself was built.
- **Client:** company or founder looking for a freelance or consulting engineer.
- **Secondary:** developers studying the code or adapting it, including Brazilian developers from Instagram.

### User stories and acceptance criteria

**S1. Ask and get a cited answer.** As an evaluator, I want to ask a question about André's career or
projects and get an answer that cites its sources, so that I can check his claims instead of trusting them.

- The first screen invites a question and shows 4–6 example questions in the interface language (proposed
  by Claude and reviewed by André); switching the language switches the examples; clicking one asks it.
- A draft of the answer streams in, marked "checking against sources"; its first words appear within 3
  seconds (p95, visitors in the US). When checking ends, the checked claims replace the draft and any
  unsupported claim is shown struck through as "removed: not supported by the sources". If checking can't
  finish in time, the draft is replaced by an abstention with the sources found.
- Every claim of the answer carries a numbered citation; clicking it shows the passage, the source's title
  and a link when the source is public. Each cited source is one the system retrieved for that question.
- A question only partly covered gets an answer for the covered part and one sentence saying which part
  is not in the sources.
- By default the answer is short (about 150 words); when the visitor asks for more detail, up to about 400
  words, and beyond that it suggests opening the sources.
- A cached answer (verified earlier for an almost identical question) shows the step "reused a verified
  answer to the same question" and is labeled as cached.

**S2. Ask about how the site was built.** As a technical evaluator, I want to ask about the architecture,
its decisions and trade-offs, and the development workflow, so that I can judge the engineering behind it.

- Questions about the site are answered from its design documents, decision records and code map, with
  citations, under the same rules as S1.

**S3. Watch the answer being produced.** As an evaluator, I want to see the steps of the answer live (for
example: understanding the question, searching, checking the answer), so that I can see the system works
and isn't a canned reply.

- Each step appears as it starts and is marked done when it ends, before the answer finishes.
- Step names are readable by a non-engineer in the visitor's language.

**S4. Honest "not in my sources".** As an evaluator, I want the site to say when its material doesn't cover
my question, so that I can trust what it does say.

- When the corpus doesn't support an answer, the site says "not in my sources" (or the Portuguese
  equivalent) instead of guessing (an abstention), and suggests the closest example question from the
  first-screen list, plus the contact links.
- Salary expectations, availability, visa and time zone: answered only from what the FAQ states; anything
  else on these topics is an abstention that points to contacting André directly. Never guessed.
- A follow-up with no earlier context (first question, or after the conversation started over) is an
  abstention: "I don't have earlier context in this conversation — what are you asking about?", with the
  example questions.

**S5. Polite decline.** As a visitor, I want off-topic or out-of-bounds requests declined politely, so that
the site stays focused and safe.

- General-purpose requests (write my code, unrelated topics) are declined with one sentence and the
  closest example question from the first-screen list.
- Personal life, and opinions about former employers or colleagues, are declined politely with a
  suggestion of an answerable question.

**S6. Follow-up questions.** As an evaluator, I want to ask a follow-up ("and what was the result?") that
takes my earlier questions into account, so that I can dig in without repeating context.

- A follow-up in the same conversation is understood in context of the previous questions and answers.
- A conversation lives in one browser tab: reloading keeps it, a new tab starts a new one, and after 30
  minutes idle the next question starts over, with a "new conversation" notice.
- A "new conversation" button clears the context at any time.
- Nothing is remembered across conversations.

**S7. Bilingual.** As a visitor, I want to ask in English or Portuguese and get the answer in the same
language, so that I can use my own.

- A question in Portuguese is answered in Portuguese even when the sources are in English (for example, the
  résumé), and vice versa.
- A question in another language gets an answer in English, with one line saying the site answers in
  English and Portuguese.
- The interface text is available in both languages.

**S8. Works when the AI doesn't.** As a visitor, I want the site to keep working when the AI is down or the
daily budget cap is reached, so that a visit is never a dead end.

- In degraded mode the visitor gets up to 6 matching passages, each with its source's title and link,
  under a notice saying why there is no written answer.
- In degraded mode, the first question of a conversation can still get a cached answer, at no cost.
- When nothing matches, the notice says "no matching sources for this question" and shows the example
  questions and the contact links.
- When the whole backend is unreachable (for example, during a deploy), the page says so instead of
  failing silently.
- Reaching the budget cap never causes extra spending.

**S9. Reach André.** As a client or evaluator, I want contact links right after an answer, so that I can
reach out while I'm convinced.

- Contact links (LinkedIn, GitHub and email, in this order) are visible after every answer and in
  abstentions, declines and degraded-mode replies.

**S10. Fair use.** As a visitor, I want a clear message when I'm sending too many questions, so that I know
what happened and when I can continue.

- Bots and scripted traffic are stopped before they spend the model budget, without a puzzle for a normal
  visitor.
- A visitor over the rate limit gets a clear message saying when they can ask again.

**S11. Works without 3D.** As a visitor on a phone or a device without 3D graphics, I want the full
question-and-answer experience, so that the site works for me too.

- Without 3D graphics support, or with reduced motion on, asking, answers, citations and live steps work
  the same; the animated hero is replaced by a still image.

### Non-goals

- Opening an answer's public trace, and the status, budget and sources pages (F02).
- The evals page (F03).
- Agent tools beyond retrieval, and the public page of blocked attempts (F04). The defenses themselves
  (bot check, rate limit, prompt-injection containment) are in this PRD; only the public page is not.
- Memory across conversations, accounts, saved conversations.
- Answers from outside the corpus (no web search).
- Actions on the visitor's behalf (sending email, booking calls); contact form or scheduling link.
- Languages other than English and Portuguese.

## 3. AI system requirements

### What the agent can do

- Reads only the reviewed corpus: résumé, career narrative, FAQ, project documents and the site's own design
  documents, decision records and code map.
- Understands the question (topic, language, follow-up context), searches the corpus, writes an answer from
  what it found and checks the answer against the sources before showing it.
- Answers in the visitor's language, cites sources, abstains when the sources don't support an answer.
- Never: answers from outside the corpus, guesses about salary, availability or private matters, follows
  instructions found inside sources or questions that try to change its rules, reveals its hidden
  instructions, acts on the visitor's behalf, or spends past the daily budget cap.

### Evaluation strategy

- **Eval set:** fixed questions with expected answers and expected sources, in both languages, covering:
  career facts, projects, how the site was built, follow-ups, questions outside the corpus (expected
  abstention), off-topic and out-of-bounds requests (expected decline), and prompt-injection attempts.
- **Metrics and targets:**
  - Faithfulness ≥ 0.90 (product brief metric).
  - Citation validity: every cited source was retrieved for that question. Target: 100%.
  - Abstention: questions outside the corpus get "not in my sources". Target: ≥ 0.90.
  - Decline: off-topic and out-of-bounds requests are declined. Target: ≥ 0.90.
  - Language match: the answer is in the question's language. Target: 100%.
  - First words within 3 s, p95, measured from the US.
- **Gate:** no change ships without passing the eval set.
- **Live check:** a nightly subset runs on the same free models visitors get, and a regression alerts André.

## 4. Product risks

| Risk | Mitigation |
| --- | --- |
| A wrong or embarrassing answer in public | Answers only from reviewed material; checked against sources before shown; abstains when unsure; eval gate before any change ships |
| Visitors don't ask | Example questions on the first screen; a question box as the first thing on the page |
| A private detail leaks through an answer | The corpus holds only material André reviewed and approved for publication |
| Free models hit limits or go down | Fallback across providers; degraded mode with search when none is available |
| Bots drain the budget | Bot check, rate limit, daily budget cap; at the cap the site degrades instead of spending |
| Portuguese answers worse than English ones | Eval set in both languages; language match measured |
| Slow first words make the visitor leave | 3 s p95 target measured; steps shown live while the answer is produced |

## Open questions

None at this time.

