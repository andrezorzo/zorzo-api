# Smoke check (real Turnstile widget) and screenshots

## Real widget, end to end

2026-11-25, on the F01-T04 branches of both repos. The API ran locally over a scratch copy of the fixture
database (`EMBEDDER=fake RERANKER=fake APP_ENV=local`) with Turnstile's always-pass test secret; the page was
served by `zorzo-site`'s `tools/serve.py`, so it loaded the real `challenges.cloudflare.com` script with the
invisible always-pass test site key (the Playwright suite replaces that script with a stub). Headless
Chromium asked two questions.

| Check | Result |
| --- | --- |
| No request to Cloudflare before the first interaction with the box | 0 requests on load |
| Each question carries a token from the widget | 2 requests, each with `XXXX.DUMMY.TOKEN.XXXX`, the test site key's token |
| The gate passes both | Both streams opened (search-only passages) |
| The invisible widget's container takes no room | No layout change |
| The API's log has no visitor address | Only the bind line (`Uvicorn running on http://127.0.0.1:8000`) mentions an address |

Found on the way: the always-pass test secret accepts any token, not only the dummy one (checked against the
real `siteverify`); the suite's stand-in and LLD "Development environment" follow the real behavior.

## Screenshots

Playwright with the Turnstile stub and the gate's refusals served by interception.

| File | What it shows |
| --- | --- |
| `screenshots/invalid-input-1440.webp`, `screenshots/invalid-input-390.webp` | `422`: the message under the box, no answer panel |
| `screenshots/rate-limited-1440.webp`, `screenshots/rate-limited-390.webp` | `429` with `Retry-After: 300`: "You can ask again in 5 minutes" in the answer panel |
