"""AC5, AC7, AC9 (and the stream side of AC6): `POST /api/v1/ask` read as a stream."""

import asyncio
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from rag_core.events import StreamEvent

from tests.conftest import api_settings, build_app, create_migrated_database, drop_database
from tests.contract.sse import Stream, read
from tests.fakes.openrouter import FakeOpenRouter
from tests.fakes.turnstile import DUMMY_TOKEN

QUESTION = {
    "q": "Which AWS services has he used?",
    "session": "7b9e5c1e-6f0a-4a43-9a51-2d1f1c3b8a10",
    "turnstile": DUMMY_TOKEN,
}


@pytest.fixture
def client(corpus_database: str) -> Iterator[TestClient]:
    with TestClient(build_app(api_settings(corpus_database))) as c:
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
    # The fake's default reply isn't route JSON: the models can't answer, so the run goes search-only.
    stream = ask(client)
    assert stream.names()[-3:] == ["degraded", "retrieval", "done"]
    stages = [(e.data["name"], e.data["status"]) for e in stream.only("stage")]
    assert stages == [
        ("route", "start"), ("route", "end"),
        ("retrieve", "start"), ("retrieve", "end"), ("rerank", "start"), ("rerank", "end"),
    ]  # fmt: skip
    assert stream.only("degraded")[0].data == {"mode": "search_only", "reason": "models_unavailable"}

    retrieval = stream.only("retrieval")[0].data
    assert 0 < len(retrieval["hits"]) <= 6
    assert {h["id"] for h in retrieval["hits"]} <= active_chunk_ids(corpus_database)
    assert retrieval["hits"][0]["title"] == "FAQ"  # the Cloud section names the AWS services

    done = stream.only("done")[0].data
    assert done["cached"] is False and done["outcome"] == "degraded" and done["suggestion"] is None


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
        with TestClient(build_app(api_settings(url))) as client:
            response = client.post("/api/v1/ask", json=QUESTION)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "unavailable"
    finally:
        drop_database(url)


def test_answers_unavailable_when_the_embedding_model_differs(corpus_database: str) -> None:
    # The corpus was embedded by the fake embedder; an API with Ollama's tag must not serve it.
    with TestClient(build_app(api_settings(corpus_database, embedder="ollama"))) as client:
        response = client.post("/api/v1/ask", json=QUESTION)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "unavailable"


def test_a_failing_trace_sink_still_ends_the_stream(client: TestClient) -> None:
    class Failing:
        def record(self, event: StreamEvent) -> None:
            if event.kind == "degraded":
                raise RuntimeError("sink down")

    app: Any = client.app
    app.state.trace_sinks.append(Failing())
    try:
        stream = ask(client)
    finally:
        app.state.trace_sinks.clear()
    assert stream.names()[-1] == "done"


def test_a_failure_outside_the_search_ends_with_error_then_done(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("bug")

    monkeypatch.setattr("rag_api.ask.run_agent", broken)
    stream = ask(client)
    assert stream.names() == ["error", "done"]


def test_rejects_a_body_over_the_limit(client: TestClient) -> None:
    big = {**QUESTION, "turnstile": "t" * 20_000}
    response = client.post("/api/v1/ask", json=big)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"


def test_rejects_an_oversized_body_sent_without_a_length(client: TestClient) -> None:
    def chunks() -> Iterator[bytes]:
        yield b'{"q": "x", "session": "7b9e5c1e-6f0a-4a43-9a51-2d1f1c3b8a10", "turnstile": "'
        for _ in range(20):
            yield b"t" * 1000
        yield b'"}'

    response = client.post("/api/v1/ask", content=chunks(), headers={"content-type": "application/json"})
    assert response.status_code == 422


def hung_search(client: TestClient) -> Stream:
    app: Any = client.app
    real = app.state.embedder

    class Hung:
        tag = real.tag

        async def embed(self, texts: list[str]) -> list[list[float]]:
            await asyncio.sleep(60)
            return []

    app.state.embedder = Hung()
    app.state.limits = app.state.limits.model_copy(update={"request_deadline": 0.2, "ping_interval": 0.05})
    try:
        return ask(client)
    finally:
        app.state.embedder = real


def test_a_hung_search_in_a_degraded_mode_ends_with_error_then_done(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def degraded() -> str:
        return "models_unavailable"

    app: Any = client.app
    monkeypatch.setattr(app.state.gateway, "mode", degraded)
    assert hung_search(client).names()[-2:] == ["error", "done"]


def test_a_hung_search_in_the_agent_ends_search_only_on_the_deadline(client: TestClient) -> None:
    stream = hung_search(client)
    assert stream.only("degraded")[0].data["reason"] == "deadline"
    assert stream.only("retrieval")[-1].data["hits"] == []


# AC5 (F01-T05): the remote rerank level leads, and falls to the local one.


def hits(stream: Stream) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = stream.only("retrieval")[0].data["hits"]
    return result


def local_hits(client: TestClient) -> list[dict[str, Any]]:
    """The hits with the local level only (the pass-through fake keeps the fused order)."""
    app: Any = client.app
    chain = app.state.rerankers
    app.state.rerankers = app.state.local_rerankers
    try:
        return hits(ask(client))
    finally:
        app.state.rerankers = chain


def test_remote_rerank_leads(client: TestClient) -> None:
    app: Any = client.app
    fake: FakeOpenRouter = app.state.openrouter
    fake.rerank_scores = lambda docs: [float(i + 1) for i in range(len(docs))]  # the reverse of their order
    local = local_hits(client)
    remote = hits(ask(client))
    [request] = [r for r in fake.requests if r.path == "/rerank"]
    assert request.path == "/rerank" and request.model == "test/rerank:free"
    n = len(request.body["documents"])
    assert n > 1
    assert [h["score"] for h in remote] == [float(n - i) for i in range(min(n, 6))]
    assert remote[0]["id"] != local[0]["id"]


@pytest.mark.parametrize("failure", ["error", "hang", "malformed"])
def test_falls_to_the_local_reranker_when_the_remote_one_fails(corpus_database: str, failure: str) -> None:
    fake = FakeOpenRouter()
    fake.script("test/rerank:free", failure)
    app = build_app(api_settings(corpus_database), limits={"timeout_rerank": 0.2}, openrouter=fake)
    with TestClient(app) as client:
        local = local_hits(client)
        stream = ask(client)
    assert hits(stream) == local
    assert stream.names()[-1] == "done"
    assert len([r for r in fake.requests if r.path == "/rerank"]) == 1
