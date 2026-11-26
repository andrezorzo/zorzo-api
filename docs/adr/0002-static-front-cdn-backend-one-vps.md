---
status: accepted
---

# 0002 · Static front on S3 + CloudFront, backend on one always-on VPS

The front is static files, so it lives in a private S3 bucket behind CloudFront, close to visitors
everywhere and at no cost. The backend (API, dashboard, Postgres, Ollama) runs on one always-on VPS:
Contabo Cloud VPS 4 in US East (4 vCPU, 8 GB, about US$8.50/month, bought at M1), near the main audience.
It never sleeps, so a recruiter's first question of the day is as fast as any other, and the whole
backend fits the budget on one machine.

Discarded: serverless, Lambda plus a managed database (keeping one instance warm costs about US$11/month
on its own, a second concurrent visitor still gets a cold start, and Postgres and Ollama would need paid
services outside it); PaaS free tiers such as Render, Fly or Railway (they sleep or offer too little RAM
for Postgres, Ollama and two Python apps, so the first question pays a cold start); everything on the VPS,
front included (no CDN for distant visitors, the VPS also serves static traffic, and the free WAF of
ADR 0004 is lost).

Cost accepted: one machine is a single point of failure (non-goal 2); a shared-CPU VPS adds latency
variance; the VPS leaves about US$1.50/month of margin under the cap.
