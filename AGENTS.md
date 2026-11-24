# AGENTS.md

## Documents

`docs/` holds the documents that define the product, the architecture and the implementation details:

1. `docs/product-brief.md`: what and for whom. MVP scope, what stays out, success metrics.
2. `docs/hld.md`: high-level design. Architecture decisions, main flows and the system overview.
3. `docs/lld.md`: low-level design. How each part works inside: rules, values, states, tables, routes.
4. `docs/adr/`: decision records, with the alternatives discarded; one numbering for HLD and LLD.
5. `docs/roadmap.md`: the features to build, the dependencies between them and the status of each.
6. `GLOSSARY.md`, at the root: the domain vocabulary. Use its terms in documents and planning.

Each app will have its own `AGENTS.md`, with its commands, stack details and code practices, once it
exists (`apps/api`, `apps/dashboard`, `apps/ingest`).

**Where each piece of information lives**

- **What the system does** lives in `docs/`: scope, architecture, schema, contract, business rules and
  configuration.
- **Never in two places.** A fact has one owner; the other file points to it by document and section
  title, without repeating it.
- **HLD and LLD change through their skill.** When a task changes something `docs/hld.md` or `docs/lld.md`
  records, load the `hld` or `lld` skill and use its update mode before editing the file.
- **What belongs in memory files** (`AGENTS.md`, rules) is not repeated in the architecture documents;
  they point to the memory file instead.

## Planning

Feature planning and documentation live in local Markdown files, which are the project's issue tracker.
Skills that publish to a tracker write to these files, at the paths below, without triage labels.

The hierarchy is feature → task, and each feature has two documents, the PRD and the spec:

- **Feature:** a demonstrable delivery in the product, described only by what the visitor can do, with no
  technical detail. Not a phase of the product brief. The feature number is global, sequential and never
  changes once assigned. In planning documents, "feature" always means this term.
- **PRD:** the product requirements of a feature (problem, stories, acceptance criteria, non-goals), made
  by the `prd` skill. It points to the product brief instead of repeating metrics and non-functional
  requirements.
- **Spec:** how the feature will be built (implementation and testing decisions). It follows the HLD, the
  LLD and the ADRs, pointing to them.
- **Task:** a deliverable part of a feature, from the spec. Before any code, each task gets a **plan**, made
  by the `plan-task` skill, approved and committed on its own in the **plan commit**. After that the plan
  is frozen as the record of what was approved: only the owner unfreezes it, and a decision in it that
  proves wrong is fixed in the spec, the LLD or a new task. Outside the feature folder, a task is
  identified as `F<NN>-T<NN>`, like `F01-T02`.

**Folder structure**

```
docs/features/<NN>-<title>/
  prd.md
  spec.md
  tasks/
    <NN>-<title>/       ← one folder per task
      task.md           ← the task
      plan.md           ← the task's plan
      …                 ← records of the task, if needed
```

Numbers have two digits and titles are kebab-case (`01-grounded-answers`). Tasks are numbered from `01`
within each feature, in dependency order.

A task folder holds, besides the task and the plan, only records of that task, such as a manual test
script, a measurement result or the screenshots of a visual check, in Markdown or images with kebab-case
names. Code, test fixtures and configuration never go there. Only `plan.md` freezes in the plan commit;
the other records are written during implementation and go in the task's PR.

**Status**

- The status of each feature (planned → PRD ready → spec ready → in progress → done) lives in
  `docs/roadmap.md`, which also owns the list of features.
- The status of a task (to do → in progress → done) is a Status field at the top of its `task.md`. It
  becomes "in progress" in the plan commit and "done" in the PR that delivers it. A feature becomes "in
  progress" with the first plan commit of one of its tasks, and "done" with the PR of its last task.

## Full verification

The command sequence that closes a task, run once before opening the PR. The commands of each app are in
the "Full verification" section of its `AGENTS.md`. A task that creates a command the verification must run
adds it there.

## Git conventions

- **Main branch:** `main`. Never commit directly to it.
- **Work branches:** `feature/*`, `bugfix/*`, `hotfix/*` and `docs/*`, created from `main` and merged by
  pull request.
- **Commits:** conventional commits (`docs:`, `feat:`, `fix:` …), short and focused on why.
- **Review before push:** on the unreviewed diff, run `simplify`, then `code-review` at level `high`; no code
  reaches GitHub until the review comes back with nothing accepted, plus a `security-review` when the diff
  touches logs, secrets, configuration, errors or authentication. Fixes from a review are new code and go
  through the same loop. When a library default affects security (logging, tracebacks, error rendering), check
  it in the installed source, not from memory.
- **Pull request description:** follows `.github/pull_request_template.md`. A task PR has the task title, a
  summary, the plan path, a "Criteria" section with one item per `AC<n>` (the test that proves it, or what
  a human reviewer checks and where) and optional "Notes". Any other PR has "What", "Why" (where it came
  from, e.g. a review) and "Review" (the files and sections to look at).
- **No AI attribution:** no co-author trailers or "generated with" lines in commits or pull requests.
- **Merge:** merging is the owner's decision. An agent merges only when the owner explicitly authorizes
  that PR or task, and a refused `gh pr merge` is never worked around.

## Library documentation

When you need a library's documentation, use context7 for current and accurate information.

## Agent skill usage

Always load the skills the task needs. When a skill disagrees with `AGENTS.md` or the rules, `AGENTS.md`
and the rules win.
