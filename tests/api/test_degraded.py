"""AC3 and AC4: with the budget at the cap or every `answer` entry open, questions go search-only with no
model call, the health check says `degraded`, and André is alerted once."""

import asyncio
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from rag_core.services.gateway import ModelsUnavailable, RequestSpend

from tests.api.test_ask import QUESTION, ask
from tests.conftest import api_settings, build_app

ANSWER_MODELS = ("test/answer-a:free", "test/answer-b:free", "test/answer-paid")


def reset(url: str) -> None:
    with psycopg.connect(url) as conn:
        conn.execute("DELETE FROM budget_daily")
        conn.execute("DELETE FROM alert_states")


@pytest.fixture
def client(corpus_database: str) -> Iterator[TestClient]:
    reset(corpus_database)
    with TestClient(build_app(api_settings(corpus_database))) as c:
        yield c
    reset(corpus_database)


def budget_at_the_cap(url: str) -> None:
    with psycopg.connect(url) as conn:
        conn.execute(
            "INSERT INTO budget_daily (day, cost_usd, paid_disabled_at, degraded_at) "
            "VALUES ((now() AT TIME ZONE 'UTC')::date, 0.10, now(), now()) ON CONFLICT (day) DO UPDATE SET "
            "cost_usd = 0.10, paid_disabled_at = now(), degraded_at = now()"
        )


def open_every_answer_entry(client: TestClient) -> None:
    """A gateway call against the stand-in in 429 mode opens each entry's breaker."""
    app: Any = client.app
    for model in ANSWER_MODELS:
        app.state.openrouter.script(model, "rate_limited")

    async def call() -> None:
        loop_time = asyncio.get_running_loop().time()
        with pytest.raises(ModelsUnavailable):
            await app.state.gateway.complete(
                "answer",
                [{"role": "user", "content": "q"}],
                deadline_at=loop_time + 5,
                spend=RequestSpend(0.01),
            )

    assert client.portal is not None
    client.portal.call(call)


def assert_search_only(client: TestClient, reason: str) -> None:
    app: Any = client.app
    before = len(app.state.openrouter.requests)
    stream = ask(client)
    assert stream.names()[-3:] == ["degraded", "retrieval", "done"]
    assert stream.only("degraded")[0].data == {"mode": "search_only", "reason": reason}
    assert stream.only("retrieval")[0].data["hits"]
    assert len(app.state.openrouter.requests) == before  # no model call, no remote rerank either


def test_budget_at_the_cap_answers_search_only_with_no_model_call(
    client: TestClient, corpus_database: str
) -> None:
    budget_at_the_cap(corpus_database)
    assert_search_only(client, "budget_exhausted")


def test_providers_down_answers_search_only_with_no_model_call(client: TestClient) -> None:
    open_every_answer_entry(client)
    assert_search_only(client, "models_unavailable")


def test_health_reports_degraded(client: TestClient, corpus_database: str) -> None:
    assert client.get("/api/healthz").json()["mode"] == "normal"
    open_every_answer_entry(client)
    assert client.get("/api/healthz").json()["mode"] == "degraded"
    budget_at_the_cap(corpus_database)
    assert client.get("/api/healthz").json() == {"status": "ok", "mode": "degraded", "db": "ok"}


def test_budget_at_the_cap_alerts_once(client: TestClient, corpus_database: str) -> None:
    budget_at_the_cap(corpus_database)
    for _ in range(10):
        response = client.post("/api/v1/ask", json=QUESTION)
        assert response.status_code == 200
    app: Any = client.app
    assert client.portal is not None
    client.portal.call(app.state.gateway.drain)
    assert [kind for kind, _ in app.state.alerter.sent] == ["budget_exhausted"]
