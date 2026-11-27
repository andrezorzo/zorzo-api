# Plan for F01-T11: Eval harness and activation gate

Task: [task.md](task.md) · Spec: [spec.md](../../spec.md) · PRD: [prd.md](../../prd.md)

The task builds the eval harness (core `rag_core.evals`, a new `apps/evals` command, and the six datasets
in the zorzo domain package) and puts it in front of activation: a candidate corpus version goes live only
with a linked, passing run of that version, and the eval judge never changes together with the models. It
also measures cross-lingual retrieval on its own (AC5), whose result decides whether retrieval needs more
than the multilingual vector search. Visitors see no change.

## Criteria

### AC1

**Mark:** unit test

**Scenario:** Given the zorzo domain package, when the eval set loads, then the datasets `golden`,
`unanswerable`, `adversarial`, `benign_tricky`, `multi_turn` and `facts` are present, each with cases in
`en` and in `pt`, every case id unique in its dataset and every case valid (a question or turns, the
expected outcomes, the expected sources where the dataset needs them). Given a case whose `lang` the domain
doesn't declare, a duplicate id, or a missing dataset, the harness stops before any model call, naming the
file and the field (the `eval_results.lang` guard F01-T14 left to this task). Given a case whose expected
`doc_key` is absent from the version evaluated, the run stops before any model call, naming the case.

**Planned test:** unit, `tests/evals/test_datasets.py`, "the zorzo eval set has every dataset in both
languages", "a case in a language the domain doesn't declare stops the harness", "a duplicate id stops the
harness"; integration, `tests/evals/test_harness.py`, "an expected source missing from the version stops
the run".

### AC2

**Mark:** integration test

**Scenario:** Given the fixture corpus as a candidate version, the fake provider server scripted for the
agent's stages and the eval judge, and the zorzo eval set, when `python -m rag_evals --version N` runs,
then it prints the report and stores one `eval_runs` row (`run_kind` `pre_release`, the version, its
`model_config_version`, the `judge_model`, the eval set's name and hash, and in `metrics` each metric of
LLD "6.12 Eval run" per language with its target and verdict) and one `eval_results` row per case and
layer. Given a scripted answer whose claim the eval judge marks unsupported, faithfulness drops below its
target for that case's language only, and the run fails; given a case that ends `degraded`, the run fails
whatever the metrics say; given a run cap so small that a paid call doesn't fit, the run stops, exits 1 as
not passing and stores nothing. Given every target met in both languages, the run passes. The metric functions
(recall at `RERANK_TOP_N`, MRR, faithfulness per answer, citation validity, the rates) are proved on fixed
inputs.

**Planned test:** integration, `tests/evals/test_harness.py`, "a run stores per-language metrics and
per-case results", "an unsupported claim fails faithfulness in its language", "a degraded case fails the
run", "a run that reaches its cap stops and doesn't pass"; unit, `tests/evals/test_metrics.py`, one case per metric.

### AC3

**Mark:** integration test

**Scenario:** Given a candidate, when `python -m rag_ingest --activate-version N` runs with no linked run,
with a linked run that failed, or with a linked passing run of another version, then activation is refused,
the command exits 1 naming the reason, and the version stays `candidate`. Given a passing run of that
version, which `rag_evals` links when it passes, then the version becomes `active`. Given a run that
covered only some datasets or one layer, then nothing is stored or linked. With `APP_ENV=production`, no
candidate goes live without its passing run; with `APP_ENV=local`, a candidate still activates without one
(LLD 6.1).

**Planned test:** integration, `tests/ingest/test_ingest.py`, "refuses a candidate without a passing run
of its own version", "activates a candidate with its passing run",
"activates without an eval run only when local"; `tests/evals/test_harness.py`, "a
passing run links its candidate", "a partial run stores nothing".

### AC4

**Mark:** integration test

**Scenario:** Given an active version whose linked run used judge J1 and models config C1, when a candidate
with a passing run is activated, then: with J2 and C2, activation is refused naming the rule; with J2 and
C1, and no `judge_agreement` at or above `JUDGE_AGREEMENT_MIN` in the run's metrics, it is refused; with J2,
C1 and that agreement, it is activated; with J1 and C2, it is activated. Given no active version with a
linked run (the first gated activation), any judge is accepted.

**Planned test:** integration, `tests/ingest/test_ingest.py`, "refuses a judge change together with a
models change", "refuses a judge change without its agreement", "accepts a models change with the same
judge".

### AC5

**Mark:** integration test, then document

**Scenario:** Given the fixture corpus (English `faq-en` and `site-design`, Portuguese `narrativa-pt`) and
golden cases whose expected sources are all in the other language, when the harness runs, then the report
and `metrics` carry the retrieval metrics of those cases as a `cross_lingual` subset per language, apart
from the overall ones, and a case is in the subset because of its sources' `documents.lang` in the version,
not because of a tag. Then the document part: given the measurement on the real corpus with the real
embedder and the local reranker (record `cross-lingual-retrieval.md` in this folder), the reviewer finds in
LLD "6.12 Eval run" the decision (vector search alone, `reformulate` adding search terms in the corpus
languages, or a translated full-text query), with the numbers behind it, and the open question closed. If
the harness is ready before the real corpus, that document part moves to a follow-up task and this task
delivers the integration test only.

**Planned test:** integration, `tests/evals/test_harness.py`, "cross-lingual cases are reported on their
own".

## Contract

### eval_runs · zorzo · altered

- New column `judge_model` · `text` · not null · no default: the eval judge as `<provider>/<model>`, from
  `config/evals.yaml` (LLD "6.12 Eval run"). The table has no rows yet, so no default is needed.
- `dataset` keeps its type; it now holds the eval set's name and version, `<domain>@<first 12 hex of the
  SHA-256 of the dataset files>`.

### eval_results · zorzo · altered

- New column `dataset` · `text` · not null · no default: one of the six dataset names.
- New column `scores` · `jsonb` · not null · database default `{}`: the case's metric values for its layer
  (for example `{"recall": 1.0, "mrr": 0.5}`, `{"faithfulness": 0.75, "citations_valid": true}`).
- New unique constraint `eval_results_run_case_layer` on (`eval_run_id`, `dataset`, `question_id`,
  `layer`): one row per case and layer.
- `lang` stays without a check (F01-T14): the harness validates it against the domain (AC1).

No route or stream event changes. The changes are Django model changes (`data/models.py`) with a generated
migration (`0006`, after F01-T10's `0005`; renumbered at merge if needed), not hand-written SQL.

## Contract verification

- `eval_runs.judge_model` and the eval set's name and hash written by a run: AC2; read by the activation
  rule: AC4.
- `eval_results.dataset` and `scores`, one row per case and layer: AC2.
- `eval_results_run_case_layer` refuses a second row for the same case and layer: a case in
  `tests/schema/test_migrations.py`, "one eval result per case and layer". The harness never writes one
  (AC2); the constraint backs it.

## Decisions

- **Library before own code.** Checked in the installed packages (scratch environment, 2026-11-27; none is
  in `uv.lock` except langsmith):
  - **DeepEval 4.3.6** (named by LLD "1. Code structure", Evals row). `FaithfulnessMetric.a_measure`
    extracts truths from the whole retrieval context and claims from the output text, then judges each
    claim against those truths: three judge calls per answer, plus one for the reason unless
    `include_reason=False`. It re-extracts claims from prose instead of taking our claims, and checks them
    against all passages, not the ones each claim cites, so it doesn't compute the PRD's faithfulness
    ("claims the cited sources back up"). A custom judge is a `DeepEvalBaseLLM` subclass (`generate`,
    `a_generate`), so the calls could go through our gateway. Import side effects: `deepeval/__init__.py`
    calls `autoload_dotenv()`, which reads `.env`, `.env.<APP_ENV>` and `.env.local` from the working
    directory into `os.environ` (seeding `APP_ENV` too) unless `DEEPEVAL_DISABLE_DOTENV=1`; PostHog
    telemetry is on unless `DEEPEVAL_TELEMETRY_OPT_OUT`; it registers a `pytest11` plugin and pulls
    pytest-xdist, pytest-rerunfailures, pytest-repeat, grpcio and OpenTelemetry into the one workspace
    environment, so they load in our test suite. Verdict: not used in F01 (owner's decision, 2026-11-27).
    The gated faithfulness is ours, because DeepEval's is not the PRD's metric and costs three or four
    calls per answer instead of one. DeepEval is evaluated again, isolated (its own uv project, or
    `DEEPEVAL_DISABLE_DOTENV=1` and `-p no:deepeval`), with F03's reference-free metrics (answer relevancy,
    contextual relevancy). Running its faithfulness next to ours stays a study experiment, run by hand
    (`estudo.md`). The LLD's Evals row changes, and `DEEPEVAL_TELEMETRY_OPT_OUT` leaves the variables.
  - **Ragas 0.4.4.** Its faithfulness has the same shape (statements from the answer against the whole
    context). Its ID-based context precision and recall are set arithmetic. It pulls Hugging Face
    `datasets` (pyarrow, pandas), langchain-community, langchain-openai, instructor and tiktoken.
    Discarded: what it computes that we need is a few lines, and its model calls run through its own
    LangChain or OpenAI clients, outside the gateway and its budget.
  - **promptfoo 0.126.0** (npm). A Node CLI with YAML suites. A Python provider could call our agent, but
    its graders (`context-faithfulness`, `llm-rubric`) call models through promptfoo's own providers, and
    its results land in its SQLite or JSON files, not in `eval_runs`. Discarded: a second toolchain, and
    the judge outside the gateway.
  - **inspect-ai 0.3.292.** Tasks, solvers and scorers with file logs and a viewer. A custom solver could
    call the agent, but its model API is its own, and its logs are files that would need translating into
    `eval_runs`. Discarded: our runner is a loop over cases with Postgres writes.
  - **LangSmith `aevaluate`** (langsmith 0.16.2, already installed through langchain-core). It runs a
    target over examples (`Iterable[schemas.Example]`) with our evaluators, concurrency and repetitions;
    `upload_runs=False` uploads nothing. The evaluators, the storage in `eval_runs` and the gate would
    stay ours. Uploading experiments would use the free plan's 5k traces a month that production traces
    use. Not used: it would add only the loop. Uploading experiments for `/dash/evals` is F03's (snapshot
    pull).
  - **openevals 0.2.5.** Ready LLM-as-judge prompts (RAG groundedness, helpfulness, retrieval relevance).
    The judge is a LangChain chat model or an OpenAI client, outside the gateway, and groundedness judges
    the whole output against the whole context. Discarded.
  - What stays ours: the dataset loader (Pydantic over YAML, like the other domain files), the
    deterministic metrics, the per-claim eval judge call (one batched call per answer, through the model
    gateway), the run loop and the storage.
- **The harness runs the agent in process.** `rag_evals` builds the same `AgentContext` the API builds and
  calls `run_agent` against the version given (a candidate too), with a collecting sink that keeps the
  stream events. There is no HTTP, no gate, no answer cache (F01-T09's off switch) and no stored trace
  (F01-T10's writer is a sink the API wires in, which the harness doesn't): Turnstile and the rate
  limit (40 a day) would stop a run, a cached answer would measure nothing, and eval questions must not
  reach `public_traces` or `questions_log`. Cases run one at a time, under `REQUEST_DEADLINE` each, as
  visitors' questions do.
- **Three layers, one end-to-end pass per case.** `retrieval`: each question searched and reranked alone
  (no chat model; the rerank chain only), giving recall at `RERANK_TOP_N` and MRR of the expected
  `doc_key`s. `generation`: from the end-to-end run's answer, faithfulness, citation validity and language
  match. `end_to_end`: the outcome against the case's expected outcomes. Chunk ids map to `doc_key`s through
  the version's `documents`, so expected sources survive re-ingests. A generation run with fixed context
  (the expected passages given straight to `answer`) isn't built: no criterion asks for it, and it needs a
  second entry point into the answer step.
- **Metrics and targets.** Gated, from the PRD: faithfulness at least `FAITHFULNESS_MIN`, citation
  validity `CITATION_VALIDITY_MIN`, abstention `ABSTENTION_MIN`, decline `DECLINE_MIN`, language match
  `LANGUAGE_MATCH_MIN`, each per language, and from HLD "3. Quality requirements" (security), zero leaks on
  the adversarial set. Report only, with no target: retrieval recall and MRR, the golden answer rate, the
  adversarial accepted-outcome rate, benign tricky (not blocked or declined), and time to the first `token`
  at p95. The golden answer rate is there because a system that abstains on every question would pass four
  of the five gated metrics; targets for it and for recall come after the first baseline, through a PRD
  change. F03's extra metrics (answer relevancy, context relevancy, correctness, public-view judgment)
  belong to an F03 task. The PRD's "first words within 3 s, p95, from the US" is measured in production from the
  traces (F02). The local number only orients.
- **Faithfulness is per claim, against the claim's own citations.** For each answered case, the eval judge
  gets the published claims (verdict `supported` or `partial`, with the text the visitor saw) and, for each
  claim, the passages it cites, and labels each claim backed or not in one call. The case's score is backed
  claims over published claims. The language's score is the mean over its answered cases. Removed
  (`unsupported`) claims and the `gap` sentence aren't scored: the visitor sees the first as removed and
  the second as a note.
- **Citation validity is measured even though it holds by construction** (the verify step maps passage
  numbers to this request's chunks): it guards against a regression in that mapping.
- **A run passes** when every gated metric meets its target in every language and no case ended `degraded`
  or `error`. A run with an infrastructure failure measures nothing, and a rate computed without those
  cases would look better than it is. Only a run over the whole eval set and every layer is stored, and
  only a passing `pre_release` run of a `candidate` is linked to it. A partial run (`--dataset`, `--layer`)
  prints its report and writes nothing, so a quick retrieval check (AC5) never becomes a gate.
- **Expected outcomes are a list.** `adversarial` cases accept `blocked`, `declined` or `abstained` and
  fail on `answered`. So the dataset holds before and after F01-T08 adds the guard, and F01-T08 can narrow
  a case to `blocked`. `benign_tricky` cases accept `answered` or `abstained`. A leak in any adversarial
  answer fails the run (HLD: zero leaks); the leak check looks for the canary F01-T08 adds and for the
  system prompt's text in the answer.
- **Declines and abstentions live in `unanswerable`.** That dataset holds both in-scope questions the
  corpus doesn't cover (expected `abstained`) and out-of-scope requests (expected `declined`), so
  abstention and decline are measured over the cases that expect each. `facts` holds career-fact questions
  (salary, availability, visa, time zone, explicit career numbers); its results set
  `eval_results.career_fact`, so `/dash/evals` hides a failed answer.
- **The cross-lingual subset comes from the data.** A golden or facts case is cross-lingual when every one
  of its expected sources is a document whose `lang` in the version differs from the case's. The subset's
  retrieval metrics are reported per language next to the overall ones (AC5). A tag in the YAML could drift
  from the corpus.
- **The eval judge is one pinned model, outside the models config.** It lives in `config/evals.yaml`
  (`judge: {provider, model, max_tokens, paid, price, agreement}`), is not part of `model_config_version`,
  and has no fallback: a chain would change the ruler silently in the middle of a run. The harness calls it
  through a second `ModelGateway` whose `judge` stage is that one entry. A judge failure leaves the case
  unscored and fails the run. The production `judge` stage stays in `config/models.yaml`, as part of what
  is measured.
- **AC4 applies LLD 6.1's rule.** The AC says "the answer model"; the LLD rule it cites says
  `model_config_version`, which also covers the other stages, limits and prompts. Activation compares the
  candidate's linked run with the active version's linked run: judge and config both different → refused;
  judge different alone → refused unless the run's `metrics.judge_agreement` reaches
  `JUDGE_AGREEMENT_MIN`. The harness copies `agreement` from `config/evals.yaml` into the run. The
  measurement that produces it (the judge against the owner's labeled sample) is a later task, needed at
  the first judge change; until then any judge change is refused. The first judge is accepted, as F03's PRD
  speaks of "a judge change".
- **Targets and the eval judge are not rule values of `limits.yaml`.** `limits.yaml` is hashed into
  `model_config_version`, so a target change there would invalidate the answer cache and look like a models
  change to AC4. They go to `config/evals.yaml` (`EVALS_FILE`), with `JUDGE_AGREEMENT_MIN` and
  `EVAL_RUN_BUDGET_USD`.
- **An eval run never touches visitors' budget or public logs.** The run's gateways get an in-memory
  `RunBudget` adapter (the Budget port) capped at `EVAL_RUN_BUDGET_USD` for the run, a log-only event log
  and a log-only alert store. Paid calls reserve against the run's cap, never against `budget_daily`, and
  an eval run's cooldowns open no `providers_down` incident. The free quota is the provider account's
  (decisions log, 2026-11-19), shared with visitors whatever key is used.
- **The run's cap is `EVAL_RUN_BUDGET_USD` = US$0.10** (owner's decision, 2026-11-27). Where the number
  comes from: about 160 cases, each call about 3k tokens in and 200 out, about 6 agent calls a case. If
  every agent call fell to today's paid entry (US$0.05 in, US$0.08 out per million tokens), a full run would
  cost about US$0.16, but the chain tries the free entries first, so most calls cost nothing. The eval judge
  (one call per answered case) takes a few cents. **When the cap is reached, the run stops:** the first paid
  reservation that doesn't fit ends the run after the current case, which prints its report with the
  reason, exits 1 and stores nothing. A stopped run is never a pass and can never be linked. A cap that
  stops runs often is raised in a reviewed change to `config/evals.yaml`.
- **A pre-release run uses the API's models file by default** (owner's decision, 2026-11-27): the run's
  `model_config_version` is production's, the free entries go first, and the paid entry runs last under
  the run's cap. `--models <file>` points a run at a paid mirror of the same models (decisions log,
  2026-11-19; F03's PRD, S5) when the owner wants, and the run records that file's version. Until launch,
  no visitor shares the free quota.
- **Which eval judge.** One pinned, paid model with structured output, from a family not in the answer
  chain (Gemma, Nemotron and Mistral today). The model is to be chosen at implementation, within the cap:
  about 3k tokens in and 200 out per answered case. The first judge needs no agreement measurement
  (decision above).
- **The datasets live in the domain package** (`domains/zorzo/src/domain_zorzo/evals/<dataset>.yaml`,
  ADR 0011), with the eval judge's prompt in `evals/prompts/faithfulness.md`. Case shape: `id`, `lang`,
  `question` (or `turns` in `multi_turn`), `expect: {outcomes, sources, answer}` (the `doc_key`s of the
  expected sources; the expected answer only in `facts`, shown on `/dash/evals`, not scored in F01).
- **The zorzo datasets ship against the fixture corpus** (owner's decision, 2026-11-27). The real corpus
  (`domains/zorzo/.../corpus/`, empty today) is André's. The datasets' cases, in both languages and with
  cross-lingual ones, cite the fixture corpus's documents (`faq-en`, `narrativa-pt`, `site-design`), which
  the tests ingest. A run of the real corpus stops on the missing `doc_key`s until real cases replace them
  (drafted by Claude, reviewed by André), so the real corpus can't go live on fixture cases. AC5's LLD
  decision needs the real corpus: if this task is ready first, it moves to a follow-up task (AC5's text
  says so).
- **Dataset sizes, per language:** golden 20 (at least 4 cross-lingual), unanswerable 10 abstain and 10
  decline, adversarial 10, benign tricky 10, multi turn 5 conversations, facts 8: about 160 cases. With 10
  cases, a 0.90 target allows one miss. The fixture-corpus cases are fewer; the sizes apply to the real
  cases.
- **The multi-turn dataset runs once F01-T07 has merged.** Without conversation state, each follow-up
  would run as a new question. 07 is not added to "Blocked by": the merge order (T10 → T08 → T07 → T09 →
  T11) already puts this task last, rebased on all four.
- **The ingest keeps activation, the eval command links.** The flow is `rag_ingest` (candidate) →
  `rag_evals --version N` (stores the run, links it when it passes) → `rag_ingest --activate-version N`
  (the gate, then publish). Activation and publishing stay in one app. `rag_evals` exits 0 on a passing run
  and 1 on a failing run or any failure, with one JSON summary line, like the ingest.
- **Noise band and the PR eval gate stay out.** HLD flow 6.4's "regresses beyond the noise band" gates
  merges in CI, with provider keys in GitHub secrets. Activation gates on the PRD's absolute targets. The
  CI job, the noise-band rule and the nightly run (LLD "7. Background processes") are later tasks.
- **Local activation without a run stays** (owner's decision, 2026-11-27). The frozen F01-T02 plan
  made it permanent and LLD 6.1 records it: a fresh clone runs without accounts (HLD goal 5), and the local
  `qwen3:1.7b` is unlikely to reach faithfulness 0.90. AC3's text changed to match ("…; production never
  activates without one (LLD 6.1)"). `--activate` keeps working locally.
- **AC3 and AC5 texts changed** (owner-approved, 2026-11-27): AC3 said "the local-only shortcut from 02 is
  removed" and now says "production never activates without one (LLD 6.1)"; AC5 gained "If T11 is ready
  before the real corpus, the LLD decision moves to a follow-up task."
- **HLD abstention target follows the PRD.** HLD "3. Quality requirements" left the abstention target "TBD
  after the first baseline"; the approved PRD sets 0.90, and AC2 gates on the PRD's targets. The HLD row and
  its open question are updated in this plan.
- **Model calls and cost** (for the owner to authorize; keys in `infra/.env` are never printed):
  - Tests (AC1–AC5 integration and unit): the fake provider server and the fake embedder; no real call.
  - AC5's measurement: the retrieval layer only (`--layer retrieval`), with the real embedder (Ollama
    `bge-m3`, local) and the local cross-encoder (`APP_ENV=local`, empty rerank chain); no provider call.
    If the production rerank chain is wanted in the comparison, it calls OpenRouter's free Nemotron
    rerank: free quota, no money.
  - A full run of the zorzo eval set on the local profile (Ollama `qwen3:1.7b` for every stage, and an
    eval config whose judge is a local Ollama model, via `EVALS_FILE`): no provider call.
  - **Billable or quota-spending:** a full run with the production models file (free entries first, then
    the paid entry up to `EVAL_RUN_BUDGET_USD`) plus the paid eval judge. At about 160 cases × 6 calls this is close to the 1,000 free requests a day of the OpenRouter
    account, and at most the run's cap in money. The first such run, and any run before activating the
    real corpus in production, needs the owner's go-ahead. Measuring a judge's agreement (later task) also
    calls the judge.

## Cross-task overlaps

Settled across the F01 plans (2026-11-27). This plan follows them:

- **Merge order T10 → T08 → T07 → T09 → T11.** This task rebases on all four before its PR.
- **`run_agent`** (F01-T07 session and history, F01-T09 cache step, F01-T10 trace sinks): the harness calls
  it in process with a new conversation per case, with F01-T09's cache switch off, and wires none of the
  API's trace sinks (F01-T10's writer stays a sink the API wires in).
- **`done`** gains optional fields: `contact` and `suggestion` (F01-T08), `notice` (F01-T07), `cached`
  (F01-T09). The harness reads `outcome`; a cached answer can't occur, since the cache is off.
- **`model_config_version`** hashes the prompts and the intents (F01-T09), so `config_version` takes them;
  `eval_runs` records what it returns, and AC4 compares it.
- **`error` is a stored outcome** (F01-T10). An eval case ending `error` fails the run (decision above).
- **Migrations:** F01-T10 takes `0005`; this task's is `0006`, renumbered at merge if needed.
- **The local language detector** (F01-T09, degraded-mode cache lookup): language match uses the same
  library.
- **LLD sections several plans edit:** "4. Data", "Configuration", "9. Values and configuration" and "12.
  Open questions"; this task's edits rebase on the others'.

## Files and interfaces

- `packages/core/src/rag_core/evals/__init__.py` (new).
- `packages/core/src/rag_core/evals/datasets.py` (new): `Expect`, `Turn`, `Case`, `Dataset`, `EvalSet`
  (Pydantic, frozen); `DATASETS` (the six names); `load_eval_set(domain, languages) -> EvalSet`, with
  `EvalSet.version` (name and hash); `DomainError` on a bad file.
- `packages/core/src/rag_core/evals/config.py` (new): `EvalConfig` (`judge` entry, `targets`,
  `run_budget_usd`, `judge_agreement_min`), `load_eval_config(path)`.
- `packages/core/src/rag_core/evals/metrics.py` (new): pure functions `recall_at_k`, `reciprocal_rank`,
  `faithfulness`, `citations_valid`, `rate`, `p95`; `aggregate(results, languages, targets) -> Metrics`;
  `passed(metrics, results) -> bool`.
- `packages/core/src/rag_core/evals/judge.py` (new): `judge_claims(gateway, prompt, claims) ->
  list[bool]`, one schema-bound `judge` call.
- `packages/core/src/rag_core/evals/runner.py` (new): `CollectingSink`; `run_eval(eval_set, version, *,
  agent, context, retrieval services, judge, store) -> RunReport`; `RunReport.summary()`.
- `packages/core/src/rag_core/ports/__init__.py`: `EvalStore` protocol (`sources(version, chunk_ids)`,
  `document_langs(version, doc_keys)`, `record(run, results) -> int` in one transaction, `link(version,
  run_id)`); `CorpusStore.eval_passed` → `activation_refusal(version) -> str | None`.
- `packages/core/src/rag_core/adapters/eval_store.py` (new): `PgEvalStore`.
- `packages/core/src/rag_core/adapters/run_budget.py` (new): `RunBudget(cap)`, the Budget port in memory;
  log-only `EventLog` and alert store for eval runs.
- `packages/core/src/rag_core/adapters/pgvector_store.py`: `activation_refusal` (own version, passed, the
  judge rule of AC4).
- `packages/core/src/rag_core/ingest/pipeline.py`: `activate` raises `ActivationRefusedError` with the
  store's reason.
- `apps/evals/` (new workspace member `rag-evals`): `pyproject.toml`, `src/rag_evals/__main__.py`
  (`python -m rag_evals --version N [--dataset NAME …] [--layer retrieval|generation|end_to_end] [--models
  FILE]`, one JSON summary line, exit 0 or 1), `src/rag_evals/settings.py` (`EvalSettings(AppSettings)`:
  `models_file`, `evals_file`), `AGENTS.md` (commands, cost note, full verification pointer).
- `apps/ingest/src/rag_ingest/__main__.py`: the refusal reason in the log; `--activate` unchanged.
- `config/evals.yaml` (new): the eval judge, targets, `run_budget_usd`, `judge_agreement_min`.
- `domains/zorzo/src/domain_zorzo/evals/{golden,unanswerable,adversarial,benign_tricky,multi_turn,facts}.yaml`
  and `evals/prompts/faithfulness.md` (new).
- `apps/dashboard/data/models.py`: `EvalRun.judge_model`; `EvalResult.dataset`, `scores`, the unique
  constraint; `data/migrations/0006_*.py` generated (renumbered at merge if needed).
- `pyproject.toml`: workspace member and source `rag-evals`; `rag_evals` added to the import-linter
  contracts' source modules (the count of contracts stays 4).
- `tests/evals/` (new): `test_datasets.py`, `test_metrics.py`, `test_harness.py`;
  `tests/fixtures/evals.test.yaml` (eval judge on the fake server); `tests/conftest.py`: `run_evals(...)`
  next to `run_ingest`.

## Existing tests that change

- `tests/ingest/test_ingest.py`: "activates without an eval run only when local" also checks, in
  production, that the linked run must be of that version and must pass.
- `tests/schema/test_migrations.py`: the reflected columns of `eval_runs` and `eval_results`, and the new
  unique constraint.

## Data change

None. `eval_runs` and `eval_results` have no rows in any environment yet, so `judge_model` and `dataset`
are added without defaults.

## Updated documents

- `docs/lld.md`:
  - "1. Code structure": `apps/evals/` and `config/evals.yaml` in the tree.
  - "2. Components and dependencies": the Apps row names `apps/evals`; new port row EvalStore.
  - "Configuration": new "Eval config" item (`config/evals.yaml`, outside `model_config_version`).
  - "4. Data": `eval_runs` and `eval_results` notes with the planned columns, one result per case and layer.
  - "6.1 Corpus version": the linked run must be of that version; how activation applies the judge rule.
  - "6.12 Eval run" (new): eval set, how a case runs, layers and metrics, cross-lingual subset, pass rule,
    the eval judge, spend.
  - "9. Values and configuration": the five targets, `EVAL_RUN_BUDGET_USD` (US$0.10 per run) and
    `JUDGE_AGREEMENT_MIN` now in `config/evals.yaml`; `EVALS_FILE` and the `evals` project on the
    variables it reads.
  - "12. Open questions": the eval harness item narrowed to the noise band; new item for cross-lingual
    retrieval (AC5).
  - "1. Code structure", Evals row: plain Python metrics and our per-claim eval judge; DeepEval evaluated
    again, isolated, with F03's metrics.
  - "9. Values and configuration": `DEEPEVAL_TELEMETRY_OPT_OUT` removed.
- `docs/hld.md`, "3. Quality requirements": the abstention target is the PRD's ≥ 0.90; the matching item in
  "12. Open questions" closed.
