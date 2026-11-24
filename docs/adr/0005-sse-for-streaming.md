---
status: accepted
---

# 0005 · SSE for streaming pipeline events

The stream is one-way: the question goes up once, then the server sends step events, tokens and the final
answer. The ask call is a plain POST that returns an SSE stream, with a heartbeat every 10 s to keep quiet
steps under CloudFront's origin timeout. Because the request is ordinary HTTP, the WAF, the rate limit and
the Turnstile token all act on it directly. The event format is a JSON Schema contract, independent of
the transport.

Discarded: WebSocket (works through CloudFront and is the fallback if SSE gets buffered, but opens a
two-way connection the one-way stream doesn't need, and moves the token and the rate limit out of a plain
POST); polling (more requests, and the steps are no longer live).

Cost accepted: whether CloudFront passes SSE event by event is a hypothesis until the M1 test; a dropped
stream is not resumed, the visitor asks again.

Interview answer: "The stream only goes one way, so SSE: a plain POST, so the WAF, rate limit and bot
token work on it as usual. WebSocket is my fallback, with the same event contract, if CloudFront buffers
the stream."
