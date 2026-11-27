"""With the budget at the cap, or every free `answer` entry cooling down while the paid entry can't run,
questions go search-only with no model call, the health check says `degraded`, and André is alerted once
(F01-T05 AC3, AC4; F01-T13 AC5)."""

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from rag_core.services.gateway import ModelsUnavailableError, RequestSpend

from tests.api.test_ask import QUESTION, ask
from tests.conftest import api_settings, build_app, reset_tables, seed_budget

FREE_ANSWER_MODELS = ("test/answer-a:free", "test/answer-b:free")


@pytest.fixture
def client(corpus_database: str) -> Iterator[TestClient]:
    reset_tables(corpus_database, "budget_daily", "alert_states")
    with TestClient(build_app(api_settings(corpus_database))) as c:
        yield c
    reset_tables(corpus_database, "budget_daily", "alert_states")


def budget_at_the_cap(url: str) -> None:
    seed_budget(url, 0.10, paid_disabled=True, degraded=True)


def cool_every_free_answer_entry(client: TestClient, url: str, behavior: str) -> None:
    """Past the paid cutoff, a gateway call with every free `answer` entry failing puts each in cooldown."""
    seed_budget(url, 0.08, paid_disabled=True)
    app: Any = client.app
    for model in FREE_ANSWER_MODELS:
        app.state.openrouter.script(model, behavior)

    async def call() -> None:
        loop_time = asyncio.get_running_loop().time()
        with pytest.raises(ModelsUnavailableError):
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


def test_providers_down_answers_search_only_with_no_model_call(
    client: TestClient, corpus_database: str
) -> None:
    cool_every_free_answer_entry(client, corpus_database, "unauthorized")
    assert_search_only(client, "models_unavailable")


def test_free_limits_reached_answer_search_only_as_quota_exhausted(
    client: TestClient, corpus_database: str
) -> None:
    cool_every_free_answer_entry(client, corpus_database, "rate_limited")
    assert_search_only(client, "quota_exhausted")


def test_cooled_free_entries_keep_the_mode_normal_while_the_paid_entry_can_run(
    client: TestClient, corpus_database: str
) -> None:
    cool_every_free_answer_entry(client, corpus_database, "rate_limited")
    seed_budget(corpus_database, 0.0)  # the paid entry can run again
    assert client.get("/api/healthz").json()["mode"] == "normal"


def test_health_reports_degraded(client: TestClient, corpus_database: str) -> None:
    assert client.get("/api/healthz").json()["mode"] == "normal"
    cool_every_free_answer_entry(client, corpus_database, "unauthorized")
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
