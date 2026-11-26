---
status: accepted
---

# 0010 · Two repositories, with brain data delivered through an S3 manifest

Front and back live in separate public repositories, as most companies keep them: `zorzo-site` (static
site, brain, contract tests, S3 deploy) and `zorzo-api` (API, dashboard, ingest, corpus, evals,
Terraform, system docs). Their contact points are explicit: the backend publishes the brain and corpus
data to S3 under hashed paths with a manifest the front reads, so a corpus change needs no front commit or
deploy; the SSE event contract lives in the backend as a JSON Schema the front tests against, and a
breaking change needs a new API version.

Discarded: a monorepo (one PR can change both sides and setup is simpler, but deploys couple and it looks
less like how teams work); brain data committed to the front repository (a cross-repository commit for
every corpus version).

Cost accepted: more ceremony (a versioned contract, two CI pipelines) and a scoped AWS credential on the
VPS so it can write to the `data/` prefix.
