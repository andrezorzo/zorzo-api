---
doc_key: site-design
title: How this site works
kind: site_doc
lang: en
authority: 2
reviewed_by_owner: true
---
# How this site works

## Retrieval

Each question runs a hybrid search that fuses vector similarity and full-text matching with reciprocal
rank fusion, then reranks the best passages with a local cross-encoder.

## Verification

Every claim of an answer carries a citation to the passage that backs it, and unsupported claims are
removed before the answer is shown.
