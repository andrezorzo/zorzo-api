---
status: accepted
---

# 0006 · The agent is a bounded LangGraph state machine, not a free ReAct loop

LangGraph is imposed (it is one of the tools the site exists to show), and it supports both styles, so the
decision is how to use it. The agent is a graph of fixed steps (gate → guard → route → retrieve → assess
→ answer → verify → emit) with conditional edges. The model decides only at defined branches (route,
assess) with structured output, and every detour has a cap: at most one query rewrite, one repair, three
tool calls and 20 s per request. Three forces drive this: cost and quota (the free tiers allow about 20
requests per minute and the budget is under US$10/month, so calls per question need a known ceiling);
correctness (the biggest risk is a wrong answer about André in public, so verification must never be
skipped); and transparency (every transition is an SSE event the front shows live, a trace step and a
unit of test and eval).

Discarded: a single fixed chain, retrieve → prompt → answer (cheapest and predictable, but it can't decline
off-topic questions, rewrite a weak search, verify and repair an answer, use tools or pick another path,
so the biggest risk goes undefended); a free ReAct loop, where the model picks each next step until it
decides to stop, as in LangGraph's prebuilt `create_react_agent` (little code and good with unexpected
questions, but calls per question are unbounded, which burns the free quota and the budget; the path
changes between runs, which makes evals and tests unstable; the model may skip verification; a prompt
injection can steer tool use; and the front has no stable steps to show).

Cost accepted: more code up front, and questions that fit none of the designed paths end in abstention or
a decline instead of an improvised answer. A new capability means a new node or path (the career graph,
ADR 0011's domain parts), not a prompt change.

When ReAct would be the right call: open-ended tasks (coding, research), many tools, users who accept more
latency and cost, and mistakes that are cheap to correct. It is the default in most agent frameworks and
common in production; teams with a narrow, well-defined task often move from it to constrained workflows
as cost, latency and unpredictable answers show up ("workflows vs agents" in Anthropic's "Building
effective agents"). Hybrids are common too: this graph's tool step is itself a small ReAct loop capped at
three calls. A planned experiment measures both versions on the same eval set.

Interview answer: "I know ReAct is the default. I used LangGraph as a bounded state machine instead: the
model decides only at defined branches, every loop has a cap, so cost and latency have a ceiling on a free
quota, verification can't be skipped when the answer is about me, and every transition is an event I can
trace, test and show live. For open-ended tasks I'd pick ReAct."
