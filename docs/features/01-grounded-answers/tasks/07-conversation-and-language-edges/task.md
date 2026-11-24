# 07: Conversation and language edges

**Status:** to do

**What to build:** A visitor asks follow-ups that use earlier questions, starts a new conversation when they want, and gets sensible replies for follow-ups without context and for questions in other languages.

**Decisions consumed from other tasks:** The `route` output fields (06); the request shape (02).

**Blocked by:** 06 Verified answers with citations.

- [ ] **AC1** · Conversation state lives in the LangGraph Postgres checkpointer keyed by the session id, within the history limits of LLD "Values and configuration".
- [ ] **AC2** · A follow-up with no history is an abstention with the fixed message asking what the visitor means.
- [ ] **AC3** · A question in a language other than English or Portuguese is answered in English with one line saying the site answers in both.
- [ ] **AC4** · Answers aim at `ANSWER_WORDS_DEFAULT` words and reach `ANSWER_WORDS_MAX` when the visitor asks for more detail.
- [ ] **AC5** · The page has a "new conversation" button that creates a new session id, and after `SESSION_TTL` idle the next question starts a new one with a notice (LLD "Front against the API").
