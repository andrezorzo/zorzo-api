"""AC5, AC7, AC9 (and the stream side of AC6): `POST /api/v1/ask` read as a stream."""

import asyncio
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from rag_api.main import create_app
from rag_core.events import StreamEvent

from tests.conftest import api_settings, create_migrated_database, drop_database
from tests.contract.sse import Stream, read

QUESTION = {
    "q": "Which AWS services has he used?",
    "session": "7b9e5c1e-6f0a-4a43-9a51-2d1f1c3b8a10",
    "turnstile": "t",
}


@pytest.fixture
def client(corpus_database: str) -> Iterator[TestClient]:
    with TestClient(create_app(api_settings(corpus_database))) as c:
        yield c


def ask(client: TestClient, body: dict[str, Any] | None = None) -> Stream:
    with client.stream("POST", "/api/v1/ask", json=body or QUESTION) as response:
        assert response.status_code == 200, response.read()
        assert response.headers["content-type"].startswith("text/event-stream")
        return read(response.read().decode())


def active_chunk_ids(url: str) -> set[int]:
    with psycopg.connect(url) as conn:
        rows = conn.execute(
            "SELECT c.id FROM chunks c JOIN corpus_versions v ON v.version = c.corpus_version "
            "WHERE v.status = 'active'"
        ).fetchall()
    return {r[0] for r in rows}


def test_streams_search_only_passages(client: TestClient, corpus_database: str) -> None:
    stream = ask(client)
    assert stream.names() == ["stage", "stage", "stage", "stage", "degraded", "retrieval", "done"]
    stages = [(e.data["name"], e.data["status"]) for e in stream.only("stage")]
    assert stages == [("retrieve", "start"), ("retrieve", "end"), ("rerank", "start"), ("rerank", "end")]
    assert stream.only("degraded")[0].data == {"mode": "search_only", "reason": "models_unavailable"}

    retrieval = stream.only("retrieval")[0].data
    assert 0 < len(retrieval["hits"]) <= 6
    assert {h["id"] for h in retrieval["hits"]} <= active_chunk_ids(corpus_database)
    assert retrieval["hits"][0]["title"] == "FAQ"  # the Cloud section names the AWS services

    done = stream.only("done")[0].data
    assert done["cached"] is False and done["abstained"] is False and done["suggestion"] is None


def test_streams_no_hits_when_nothing_matches(client: TestClient) -> None:
    stream = ask(client, {**QUESTION, "q": "zyxwvut qqqq"})
    assert stream.only("retrieval")[0].data["hits"] == []
    assert stream.names()[-1] == "done"


def test_sends_pings_while_idle(client: TestClient) -> None:
    app: Any = client.app
    real = app.state.embedder

    class Slow:
        tag = real.tag

        async def embed(self, texts: list[str]) -> list[list[float]]:
            await asyncio.sleep(0.3)
            vectors: list[list[float]] = await real.embed(texts)
            return vectors

    app.state.embedder = Slow()
    app.state.limits = app.state.limits.model_copy(update={"ping_interval": 0.05})
    try:
        stream = ask(client)
    finally:
        app.state.embedder = real
    assert "ping" in stream.comments
    assert stream.names()[-1] == "done"


@pytest.mark.parametrize(
    "body",
    [
        {"session": QUESTION["session"], "turnstile": "t"},
        {**QUESTION, "q": "   "},
        {**QUESTION, "q": "x" * 501},
        {**QUESTION, "session": "not-a-uuid"},
        {**QUESTION, "turnstile": ""},
    ],
)
def test_rejects_an_invalid_request(client: TestClient, body: dict[str, Any]) -> None:
    response = client.post("/api/v1/ask", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"


def test_the_trace_sink_receives_the_streamed_events(client: TestClient) -> None:
    recorded: list[StreamEvent] = []

    class Recorder:
        def record(self, event: StreamEvent) -> None:
            recorded.append(event)

    app: Any = client.app
    app.state.trace_sinks.append(Recorder())
    try:
        stream = ask(client)
    finally:
        app.state.trace_sinks.clear()
    assert [(e.kind, e.model_dump(mode="json")) for e in recorded] == [
        (e.name, e.data) for e in stream.events
    ]


def test_streams_an_error_then_done_on_a_bug(client: TestClient) -> None:
    app: Any = client.app
    real = app.state.embedder

    class Broken:
        tag = real.tag

        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("bug")

    app.state.embedder = Broken()
    try:
        stream = ask(client)
    finally:
        app.state.embedder = real
    assert stream.names()[-2:] == ["error", "done"]
    assert stream.only("error")[0].data["code"] == "internal"
    assert "bug" not in stream.only("error")[0].data["message"]


def test_answers_unavailable_with_no_active_corpus() -> None:
    url = create_migrated_database()
    try:
        with TestClient(create_app(api_settings(url))) as client:
            response = client.post("/api/v1/ask", json=QUESTION)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "unavailable"
    finally:
        drop_database(url)


def test_answers_unavailable_when_the_embedding_model_differs(corpus_database: str) -> None:
    # The corpus was embedded by the fake embedder; an API with Ollama's tag must not serve it.
    with TestClient(create_app(api_settings(corpus_database, embedder="ollama"))) as client:
        response = client.post("/api/v1/ask", json=QUESTION)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "unavailable"
