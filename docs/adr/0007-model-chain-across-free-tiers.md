---
status: accepted
---

# 0007 · A model chain across providers' free tiers with a paid fallback

Each stage (route, answer, verify, judge) has an ordered chain: OpenRouter `:free` models, then other
providers' own free tiers (Groq, NVIDIA NIM, Google AI Studio), then OpenRouter paid within the daily
budget. A one-time US$10 OpenRouter credit raises its free daily limit (50 to 1,000 requests a day). Each
entry is pinned as a (model, provider) pair under a versioned model configuration, because the same model
can behave differently on different providers; quality is measured per provider. All are reached through
OpenAI-compatible clients, so a provider is a line of configuration.

Discarded: one paid model (stable quality and simple, but cost per question doesn't fit under
US$10/month); extra accounts to multiply free quotas (against the providers' terms and André's rule);
a self-hosted answer model on the VPS (no GPU, so the 3 s target fails; it remains the local profile and
an adaptation path).

Cost accepted: quality varies across providers and over time, so evals run nightly per provider;
free-tier terms and limits can change; visitor questions reach third-party providers, which the notice
under the input box states.
