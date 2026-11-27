# 14: Document kinds and languages from the domain

**Status:** to do

**What to build:** Visitors see no change. The document kinds and the corpus languages stop being fixed in the core and the database, and come from the domain package, like the prompts and the intents (ADR 0011); the `route` prompt stops repeating the intents, which then live only in `intents.yaml`. A new domain with other kinds of documents or other languages then needs only its own package, with no change to the core, the API or a migration. The owner's rule from the F01-T06 review: the core is a base for other projects with different domains.

**Decisions owned:** where the domain declares its document kinds and each language's full-text search configuration; what replaces the database `CHECK` constraints on `kind` and `lang`; how the `route` prompt receives the intents.

**Decisions consumed from other tasks:** The domain package and `load_domain` (02); the intents' `kinds` filter (06).

**Blocked by:** Nothing pending (02 and 06 are done).

- [ ] **AC1** · The domain package declares its document kinds; the front matter of a corpus document with a kind the domain doesn't declare fails the ingest with the file and the kind in the message, and the core names no document kind.
- [ ] **AC2** · `intents.yaml` is validated against the domain's kinds; an unknown kind still stops startup.
- [ ] **AC3** · The domain maps each of its `languages` to a Postgres text search configuration; the ingest builds each chunk's `tsvector` and the hybrid search builds its query from that map, and the core names no language. A language with a configuration Postgres doesn't have stops startup and the ingest.
- [ ] **AC4** · A migration removes the `CHECK` constraints that list zorzo's kinds and languages (`documents`, `answer_cache`, `eval_results` and any other); the ingest, the only writer of documents, is where they are checked.
- [ ] **AC5** · A test domain with other kinds and another language (fixture package) ingests and answers search-only end to end, with no change outside the fixture.
- [ ] **AC6** · Each intent in `intents.yaml` has a description, and the core fills a `$intents` slot in the `route` prompt with the names and descriptions; the prompt names no intent, so an intent added to or removed from the YAML reaches the model with no prompt edit.
