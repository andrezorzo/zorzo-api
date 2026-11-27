# Plan for F01-T14: Document kinds and languages from the domain

Task: [task.md](task.md) · Spec: [spec.md](../../spec.md) · PRD: [prd.md](../../prd.md)

The task moves the last zorzo facts out of the core and the schema: the document kinds (front matter and
`documents_kind_check`), the corpus languages and their full-text configurations (front matter,
`pgvector_store.py`, the `lang` checks) and the intent list the `route` prompt repeats. Visitors see no
change; the zorzo domain declares the same values it has today.

## Criteria

### AC1

**Mark:** integration test

**Scenario:** Given a corpus folder with a document whose front matter has a `kind` the domain doesn't
declare, when the ingest gate runs, then the run fails with a finding for that file naming the `kind` field
and the domain's kinds, and without the rejected value. Given the same document with a declared kind, the
gate passes it. Given the core's source files, when a test reads them, then none names one of zorzo's
document kinds as a string.

**Planned test:** integration, `tests/ingest/test_gate.py`, "a kind the domain doesn't declare is a front
matter problem", "the kinds come from the domain"; unit, `tests/core/test_domain.py`, "the core names no
document kind".

### AC2

**Mark:** unit test

**Scenario:** Given a domain whose `intents.yaml` names a kind absent from its `document_kinds`, when the
intents load, then a `DomainError` names the kind and the API doesn't start. Given a kind the domain
declares but zorzo doesn't, it loads.

**Planned test:** unit, `tests/core/test_intents.py`, "bad intents stop the app" (the misspelled case now
checked against the domain's kinds), "an intent reads the domain's own kinds".

### AC3

**Mark:** integration test

**Scenario:** Given a domain that maps `es` to `spanish`, when a Spanish document is ingested, then its
chunks' `tsv` is built with `spanish`, and a search whose words match it only after Spanish stemming finds
it. Given a domain mapping a language to a configuration Postgres doesn't have, then the API refuses to
start and the ingest refuses to run, both naming the configuration, before any write. Given a document whose
`lang` the domain doesn't declare, the gate fails it (as in AC1). Given the core's source files, none names a
language code or a text search configuration as a string.

**Planned test:** integration, `tests/core/test_vector_store.py`, "full text uses the domain's
configurations"; `tests/api/test_startup.py`, "stops on a text search configuration Postgres lacks";
`tests/ingest/test_ingest.py`, "refuses a text search configuration Postgres lacks"; unit,
`tests/core/test_domain.py`, "the core names no language".

### AC4

**Mark:** integration test

**Scenario:** Given the migrated database, when a row is written to `documents` with a kind or language
outside zorzo's, or to `answer_cache` or `eval_results` with another language, then Postgres accepts it.
The reflection test still matches the migrations.

**Planned test:** integration, `tests/schema/test_migrations.py`, "kinds and languages have no check in
the database".

### AC5

**Mark:** HTTP test

**Scenario:** Given a fixture domain package `domain_sample` with the kinds `manual` and `faq`, the
languages `en` and `es`, its own intents, prompts, examples and a two-document corpus (one in Spanish), when
the real ingest command runs with `DOMAIN=sample` and the API starts with it in degraded mode, then a Spanish
question streams the Spanish passage as a search-only answer, with no change outside the fixture.

**Planned test:** e2e of the API, `tests/api/test_other_domain.py`, "another domain ingests and answers
search-only".

### AC6

**Mark:** unit test

**Scenario:** Given `intents.yaml` with a `description` per intent, when the prompts load, then the `route`
system message lists every intent with its description, in file order, where the prompt has `$intents`.
Given an intent added to the YAML, it appears with no prompt edit. Given a `route.md` without `$intents`, or
an intent without a description, the API doesn't start. Given zorzo's prompts, none names an intent.

**Planned test:** unit, `tests/core/test_intents.py`, "the route prompt lists the domain's intents",
"a route prompt without the intents slot stops the app", "the prompts name no intent".

## Contract

### documents · zorzo · altered

- Removed: `documents_kind_check` (`kind IN (…)`) and `documents_lang_check` (`lang IN ('en', 'pt')`).
  The ingest, the only writer, checks both against the domain.

### answer_cache · zorzo · altered

- Removed: `answer_cache_lang_check`. The cache writer (F01-T09) stores the run's answer language, one of the
  domain's.

### eval_results · zorzo · altered

- Removed: `eval_results_lang_check`. The eval harness (F01-T11) writes the dataset's language, one of the
  domain's.

No route or stream event changes. The removals are Django model changes (`data/models.py`) with a generated
migration (`0004`), not hand-written SQL.

## Contract verification

- `documents` accepts any kind and language: AC4.
- `answer_cache` and `eval_results` accept any language: AC4.
- The checks moved to the ingest: AC1 (kind), AC3 (language).

## Decisions

- **Library before own code.** Nothing to build that a library covers: the domain values are YAML read by
  Pydantic (as today), and the text search configurations are Postgres's own (`pg_ts_config`).
- **Both lists live in `domain.yaml`.** `document_kinds: [resume, narrative, …]` and `languages` as a
  mapping from ISO 639-1 code to Postgres text search configuration (`en: english`, `pt: portuguese`); the
  first language stays the default. One file per domain fact set, read by `load_domain`, which both apps
  already call. Discarded: a `kinds.yaml` (one more file for one list) and a separate `text_search` map (two
  lists of languages that can drift).
- **The front matter takes strings; the gate checks them against the domain.** `DocumentMeta.kind` and
  `lang` become `str`, and `ingest.gate.check` gets the domain's kinds and languages and adds a finding for a
  value outside them. Discarded: a model built per domain with `create_model` (the gate already collects
  findings per file; one more check is simpler than a dynamic model).
- **Findings name the field, not the value.** The gate's rule since F01-T02 is that a finding never quotes a
  front matter value (it could be the data the gate protects); an unknown kind or language is reported as
  `invalid front matter (kind)` with the domain's kinds, which are config, not document text.
- **The full-text query is built once, from the domain's configurations.** `PgVectorStore` gets the
  configurations at construction and builds the `query` CTE with one bound `regconfig` parameter per
  language, OR-ed as today. The ingest writes each chunk with the configuration of its document's language.
  No configuration name is interpolated into SQL.
- **Configurations are checked against Postgres before serving or writing.** `check_text_search(db,
  configs)` in the adapter reads `pg_ts_config`; the API calls it in the lifespan after `reflect`, the ingest
  before its first write. A missing one raises the startup error, naming it.
- **No lookup tables instead of the checks.** A `languages` or `document_kinds` table filled from the domain
  would add a second copy of the domain and a write at every start; the ingest is the only writer of
  `documents`, and the cache and eval writers take values the domain already validated.
- **Example questions are checked too.** `examples.yaml` entries with a `lang` the domain doesn't declare
  stop the ingest, as an unknown intent kind stops the API: the first screen would show no example for it.
- **The `route` prompt is rendered once at startup.** `load_prompts(name, intents)` fills `$intents` in
  `route.md` with one line per intent (`` - `name`: description ``), in file order, and returns the filled
  prompt; the other `$slots` stay for the nodes. A missing slot or description is a `DomainError`.
  `Intent.description` is required. The descriptions move from `route.md` into `intents.yaml` word for word.
- **The "names no …" tests read string constants.** Like the intents test of F01-T06, they scan
  `packages/core/src` for zorzo's kinds, language codes and configuration names as quoted strings.
- **`model_config_version` and prompts stay as they are.** The LLD says the version hashes the prompts, but
  `config_version` hashes only models and limits today; that gap belongs to the cache (F01-T09), which reads
  the version. When it lands, it hashes the rendered `route` prompt, so a new intent description changes
  the version.
- **AC1's message names the field, not the kind** (owner-approved change of the AC text, 2026-11-27): the
  task said "the file and the kind"; it now says "the file and the field in the message, and the domain's
  kinds", per the gate rule above.

## Files and interfaces

- `domains/zorzo/src/domain_zorzo/domain.yaml`: `document_kinds`; `languages` as code → configuration.
- `domains/zorzo/src/domain_zorzo/intents.yaml`: `description` per intent.
- `domains/zorzo/src/domain_zorzo/prompts/route.md`: the intent list becomes `$intents`.
- `packages/core/src/rag_core/domain.py`: `DomainConfig.document_kinds: tuple[str, ...]`,
  `DomainConfig.languages: dict[str, str]`, `DomainConfig.language_codes` (ordered, first is the default);
  `Intent.description`; `load_intents(name, kinds)`; `load_prompts(name, intents)`; examples checked
  against the languages; no import of `ingest.frontmatter`.
- `packages/core/src/rag_core/ingest/frontmatter.py`: `kind: str`, `lang: str`; `DocumentKind` and
  `DOCUMENT_KINDS` removed.
- `packages/core/src/rag_core/ingest/gate.py`: `check(source, …, kinds, languages)` adds the findings.
- `packages/core/src/rag_core/adapters/pgvector_store.py`: `TEXT_SEARCH_CONFIG` removed;
  `PgVectorStore(db, rrf_k, min_similarity, text_search)`; `PgCorpusStore(db, text_search)` for the
  writer; `check_text_search(db, configs)`.
- `apps/api/src/rag_api/main.py`, `ask.py`: domain kinds and languages wired in; the check in the lifespan.
- `apps/ingest/src/rag_ingest/__main__.py`: the map, the check before writing, the kinds and languages to
  the gate.
- `apps/dashboard/data/models.py`: `LANGS` and the four checks removed; `data/migrations/0004_*.py`
  generated.
- `tests/fixtures/domains/domain_sample/` (new): `domain.yaml`, `intents.yaml`, `examples.yaml`,
  `prompts/*.md`, `corpus/` with an English and a Spanish document.
- `tests/conftest.py`: `run_ingest(..., domain=..., pythonpath=...)`.

## Existing tests that change

- `tests/api/test_startup.py`: `languages == ("en", "pt")` reads the codes from the new mapping.
- `tests/core/test_intents.py`: the misspelled-kind case passes the domain's kinds; the bad-intent fixture
  packages get a `domain.yaml`.
- `tests/ingest/test_gate.py`: `check` takes the domain's kinds and languages.
- `tests/core/test_route.py` and any test that loads prompts: `load_prompts(name, intents)`.
- `tests/schema/test_migrations.py`: the reflection test sees the constraints gone.

## Data change

None. Existing rows keep their values, which are all among zorzo's declared ones.

## Updated documents

- `docs/lld.md`:
  - "2. Components and dependencies", VectorStore row: full text in each of the domain's languages.
  - "Configuration": new "Domain values" item: `document_kinds`, the `languages` map, intent descriptions
    and `$intents`, and what stops the API and the ingest.
  - "4. Data": values a domain defines have no `CHECK`; the writer validates them; `documents` and `chunks`
    notes.
  - "6.1 Corpus version": a kind or language the domain doesn't declare is invalid front matter.
  - "6.3 Agent run": the route prompt gets the intents from `intents.yaml`; a question in another language
    is answered in the domain's first language.
