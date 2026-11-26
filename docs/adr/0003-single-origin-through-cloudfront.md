---
status: accepted
---

# 0003 · One CloudFront distribution as the single origin

The browser talks only to `zorzo.dev.br`. One CloudFront distribution sends `/*` to the S3 bucket and
`/api/*` and `/dash/*` to the VPS, which accepts only requests carrying CloudFront's origin secret. There
is no CORS, the WAF covers the API, and the VPS address is never the public entry point.

Discarded: an `api.` subdomain pointed straight at the VPS (CORS preflights, the VPS exposed, no WAF in
front of the API); a second distribution for the API (the Free plan of ADR 0004 covers one distribution
and one apex domain).

Cost accepted: CloudFront sits in the stream path, so SSE must survive it (ADR 0005, M1 test); its origin
timeout counts time between bytes, so quiet steps need a heartbeat; the Free plan allows five cache
behaviors, which puts every dashboard page under one `/dash/*` prefix.
