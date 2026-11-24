# 11: Eval harness and activation gate

**Status:** to do

**What to build:** No corpus or model change reaches visitors without passing the eval set: the harness runs the F01 datasets with the PRD's metrics and targets, and a corpus version is activated only with a passing run.

**Decisions consumed from other tasks:** The stream semantics of abstention and decline (06); the outcome values (01).

**Blocked by:** 06 Verified answers with citations.

- [ ] **AC1** · Datasets golden, unanswerable, adversarial, benign tricky, multi turn and facts exist in the domain package, in both languages, with fixture cases.
- [ ] **AC2** · The harness measures the metrics of the PRD's evaluation strategy with their targets, PT and EN separately, and stores runs and per-question results.
- [ ] **AC3** · A candidate corpus version is activated only with a linked passing eval run; the local-only shortcut from 02 is removed.
- [ ] **AC4** · The judge model and the answer model never change in the same version (LLD "6.1 Corpus version").
