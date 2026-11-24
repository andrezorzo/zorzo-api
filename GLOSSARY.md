# zorzo.dev.br

The professional site of André de Castro Zorzo, where visitors question an AI agent about his work and the
agent answers only from reviewed material, in public, with its controls visible.

## Material

**Corpus**:
The reviewed material the agent answers from. Not the whole internet, and not unreviewed notes.
_Avoid_: knowledge base, dataset, docs

**Source**:
One document in the corpus.
_Avoid_: file, reference

**Citation**:
A numbered marker on a claim of an answer that points to the passage of a source backing it.
_Avoid_: reference, footnote

## Conversation

**Conversation**:
The sequence of questions and answers that share context, in one browser tab, until it sits idle long
enough to start over.
_Avoid_: visit, session, chat, thread

**Answer**:
A reply written from the corpus, with a citation on every claim. It may cover only part of the question
and say which part is not in the sources.

**Draft**:
The answer text shown while it is still being checked against the sources, marked as such; checked claims
replace it and unsupported ones are shown as removed.
_Avoid_: preview, partial answer

**Cached answer**:
An answer verified earlier for an almost identical question, reused and labeled as such.
_Avoid_: canned reply

**Abstention**:
The reply when a question is in scope but the corpus doesn't cover it: "not in my sources".
_Avoid_: refusal, fallback

**Decline**:
The reply when a question is out of scope: general-purpose requests, personal life, opinions about former
employers or colleagues.
_Avoid_: refusal, block

**Block**:
A request a defense stopped (bot, injection, prompt extraction), counted in public.
_Avoid_: decline, refusal, blocked attempt

## Operation

**Degraded mode**:
The state in which the site answers with search only, because the models are down or the budget cap was
reached.
_Avoid_: offline mode, fallback mode

**Budget cap**:
The daily spending limit on models; reaching it triggers degraded mode, never extra spending.

**Trace**:
The public, redacted record of how one reply was produced. Not an analytics log of the visitor.
_Avoid_: log

## Quality

**Eval set**:
Fixed questions with expected results, used to measure quality before any change ships.
_Avoid_: test set, benchmark

**Faithfulness**:
Share of an answer's claims that the cited sources back up. Not the same as being correct about the world.
