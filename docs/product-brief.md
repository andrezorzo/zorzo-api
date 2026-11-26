# Product Brief — zorzo.dev.br

2026-11-26 · André de Castro Zorzo · Status: draft

## Overview

zorzo.dev.br is the professional site of André de Castro Zorzo, a senior backend engineer. It is not a
résumé page: visitors ask questions about his work and an AI agent answers from a curated corpus, citing
its sources. It exists to prove, in production, the skills it describes.

- **Grounded:** every answer cites the documents it came from, and the agent says "not in my sources"
  instead of guessing.
- **Transparent:** the visitor watches each step of the answer live and can open its full trace, the
  evals, the blocked attacks and the cost.
- **Bilingual:** asks and answers in English or Portuguese.
- **The controls are the demo:** known AI risks (hallucination, prompt injection, abuse, cost) are shown
  being contained, with numbers.
- **Open:** the full source is public, so the system doubles as a reference implementation: a way to
  learn the full path and a base other teams can adapt to their own domain (for example, a law firm
  searching its case files) by swapping the corpus, prompts, tools and evals.

## Problem and context

Hiring managers and recruiters can't verify a senior engineer's claims from a résumé or a LinkedIn
profile. "Built RAG systems" and "experience with LLMs" now appear on almost every profile, and they cost
nothing to write. For an engineer in Brazil applying to remote roles at US and European companies,
distance adds a trust gap on top of that. Today, visitors work around this by reading the résumé,
skimming GitHub repos (often toy projects), and running take-home tests and interviews: slow for them,
and a filter that a list of keywords doesn't pass.

Why now: demand for applied AI engineering is high, and most portfolios show a demo that works on the
happy path. Few show a system running in production with its failures, evals and costs in the open.

Hypotheses (not validated yet):

- A visitor who can question the site and inspect how it answers trusts the claims more than a résumé.
- Showing risks being contained (evals, blocked attacks, cost) sets the site apart more than a polished
  demo would.
- Visitors will ask questions instead of just scrolling, if the first screen invites it.
- The site doubles as interview material: "walk me through how this works" has a live answer.

## Target audience

- **Primary profiles:**
  1. Recruiters and engineering managers at US and European companies hiring a senior backend or
     applied AI engineer for a remote role, plus the staff engineers and tech leads who run the
     technical evaluation.
  2. Companies and founders looking for a freelance or consulting engineer for backend or applied AI
     work.

  They reach the site from his résumé, LinkedIn, GitHub or Instagram.
- **Secondary:** developers and teams who study the code or adapt it to their own domain, including
  Brazilian developers who come from Instagram to learn from the content and share it.
- **Trigger:** they are evaluating André for a role or a project and want to check his claims quickly,
  before reaching out or between interview stages.
- **Out of initial focus:** people looking for a general-purpose chatbot.

## Goals and success metrics

The targets are guesses until there is real traffic.

| Metric | What it measures | Target 90 days after launch |
| --- | --- | --- |
| Inbound opportunities | Contacts from recruiters, managers or clients that mention the site (job or freelance) | ≥ 3 |
| Ask rate | Share of visitors who send at least one question | ≥ 20% |
| Follow-up rate | Share of askers who send a second question in the same visit | ≥ 40% |
| Trace open rate | Share of answers whose public trace is opened (tests the hypothesis that inspecting builds trust) | ≥ 10% |
| Answer quality | Faithfulness on the public eval set (answers backed by the cited sources) | ≥ 0.90 |
| Monthly cost | Recurring running cost of the site (one-time model credits excluded) | < US$10/month |

## MVP scope

Platform: web (desktop and mobile browsers).

1. Ask a question about André's career and projects, or about how this site was built (architecture
   decisions, trade-offs, the development workflow), in English or Portuguese, and get a streamed answer
   that cites its sources.
2. Watch the steps of the answer live while it is produced.
3. Get a clear "not in my sources" when the material doesn't cover the question, and a polite decline
   for off-topic requests.
4. Open the public trace of any answer: sources used, scores, model, time and cost.
5. See the public dashboards: eval results, blocked attacks, system status and budget, and the list of
   sources the agent knows.
6. Keep using the site when the AI is down or the daily budget runs out (search over the sources still
   works, with a notice).
7. Reach André directly (contact links) after reading an answer.
8. Ask follow-up questions in the same visit; the answer takes what was already asked into account (no
   memory across visits).

## Out of scope

- **Event log page with solved cases:** needs real traffic to have cases. Candidate for the next phase.
- **General-purpose chat:** off-topic requests are declined.
- **Answers from outside the curated material:** no web search; if it isn't in the sources, the agent
  says so.
- **Actions on the visitor's behalf:** no sending email, no booking calls.
- **Contact form or scheduling link:** direct links are enough for now.
- **Accounts, saved conversations, memory across visits.**
- **Languages other than English and Portuguese.**
- **Visitor tracking beyond what the metrics need.**

## Non-functional requirements and constraints

| Area | Requirement |
| --- | --- |
| Cost | Recurring running cost under US$10/month; one-time model credits allowed outside the cap |
| Availability | Always on, no cold starts: the first visitor of the day gets the same speed as the others |
| Performance | First words of an answer appear within 3 seconds for visitors in the US |
| Privacy | No personal data of visitors stored; public traces are redacted; the corpus holds only material André reviewed; visitors are told before asking that answers run on providers' free tiers, which may train on their questions |
| Abuse | Bots and scripted traffic can't drain the model budget; prompt-injection attempts are contained and counted in public |
| Accessibility | Works on mobile and on devices without 3D graphics support |
| Openness | All code is public on GitHub, written in English (imposed) |
| Technology | LangChain/LangGraph (agent), LangSmith (tracing), FastAPI (API) and Django (dashboard) (imposed: the site exists to show these tools in production) |
| Deadline | Launch on 2026-12-04, 15 days from 2026-11-19 |
| Team | André alone; code produced through AI-assisted development skills |
| Budget | The recurring cap above plus a one-time US$10 model credit; no other spending |

## Alternatives and competitors

| Alternative | Strengths | Weaknesses for the audience | Our positioning |
| --- | --- | --- | --- |
| Résumé + LinkedIn | Standard, quick to scan | Claims can't be checked; AI keywords everywhere | Complements them: every claim can be questioned and traced to a source |
| GitHub profile | Real code | Takes time to read; often toy projects; nothing running | A running system, with the code public |
| Take-home tests and interviews | Direct evidence | Slow and costly for both sides | Answers "walk me through it" before the first call |
| Other "chat with my résumé" sites | Novel, interactive | Thin wrappers that guess, with no evals or controls; they only cover the résumé | Covers more than the résumé: also how the site itself was built (architecture decisions and trade-offs, the AI-assisted workflow, evals), and the controls are in public |
| Freelance platforms (Upwork, Toptal) | Vetting, reviews, payments handled | Generic profiles, platform fees, hard to stand out | Direct contact, with evidence instead of a profile |
| Referrals / doing nothing | High trust | Depends on who you already know | Reaches people outside his network |

## Risks, dependencies and assumptions

**Biggest risk:** a wrong or embarrassing answer, shown in public, hurts the reputation the site exists
to build.

| Risk | Mitigation |
| --- | --- |
| Wrong or embarrassing answer (product) | Answers come only from reviewed material; the agent refuses when unsure; every answer is traceable; no change ships without passing the eval set |
| Few visitors, or visitors who don't ask (business) | Site linked from résumé, LinkedIn, GitHub and Instagram; the first screen invites a question, with examples |
| Free models hit limits or go down (technical) | Answers fall back to other providers; when none is available, search still works, with a notice |
| Bots drain the budget (technical) | Bot check, rate limits and a daily budget cap; when the cap is reached the site degrades instead of spending more |
| Cost cap too tight: about US$1.50/month left after the server (business) | Degrade rather than overspend; review the cap after 90 days with real numbers |
| Private information leaks through an answer (product) | The corpus holds only material André reviewed and approved for publication |
| Solo maintainer with limited time; code written through AI skills (people) | Every part documented (HLD, LLD); André must be able to explain each part before it ships |
| Deadline: 15 days for the full MVP, launch on 2026-12-04 (business) | If the date is at risk, cut in this order: defenses page → evals page (evals keep running) → agent with tools (direct answers with sources stay) |

**Dependencies:** OpenRouter and other providers' free tiers (Groq, NVIDIA NIM, Google AI Studio); AWS
(CloudFront free plan, S3, Route 53); Contabo (server); LangSmith free plan; Cloudflare Turnstile (bot
check); Registro.br (domain); GitHub. Any of their free plans or terms can change.

**Assumptions:** the four hypotheses in "Problem and context", not validated yet.

## Roadmap

- **Phase 0: Corpus.** The material the agent answers from, written and reviewed by André: FAQ, career
  narrative, résumé, and the documents about how the site was built.
- **Phase 1: MVP launch (2026-12-04).** Everything in "MVP scope", built in steps (answers with sources →
  traces, status and budget → evals → agent and defenses) and launched once, only when it all works
  locally.
- **Phase 2: After launch.** Event log with solved cases (needs real traffic); career graph for precise
  factual answers; a public comparison of models, providers and rerankers. The 90-day metrics decide
  what comes first.

## Open questions

None at this time.

## Glossary

The project's vocabulary lives in [GLOSSARY.md](../GLOSSARY.md).
