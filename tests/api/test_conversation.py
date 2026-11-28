"""F01-T07 AC1 to AC3: the conversation a session keeps in the LangGraph checkpointer, the follow-up without
context and the question in another language, read from `POST /api/v1/ask` as a stream."""

import json
import re
import time
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from tests.api.test_answer import (
    ANSWER_A,
    CLAIMS,
    JUDGE,
    STANDALONE,
    outcome,
    script,
    stages,
    verdicts,
)
from tests.api.test_ask import QUESTION, ask
from tests.conftest import api_settings, build_app
from tests.contract.sse import Stream
from tests.fakes.openrouter import FakeOpenRouter, Reply

FOLLOW_UP = "And what did he build with them?"
FOLLOW_UP_STANDALONE = "What did André build with Lambda and Kinesis?"
PUBLISHED = " ".join(text for text, _ in CLAIMS)  # the first turn's answer, as `route` reads it


def follow_up(standalone: str = FOLLOW_UP_STANDALONE, language: str = "en") -> Reply:
    output = {"intent": "about_andre", "standalone_question": standalone, "language": language,
              "follow_up": True, "public_ok": True}  # fmt: skip
    return Reply(json.dumps(output))


@pytest.fixture
def fake() -> FakeOpenRouter:
    return FakeOpenRouter()


@pytest.fixture
def client(corpus_database: str, fake: FakeOpenRouter) -> Iterator[TestClient]:
    with TestClient(build_app(api_settings(corpus_database), openrouter=fake)) as c:
        yield c


def asking(session: str, q: str = QUESTION["q"]) -> dict[str, Any]:
    return {**QUESTION, "q": q, "session": session}


def route_inputs(fake: FakeOpenRouter) -> list[dict[str, Any]]:
    """What each `route` call read as its user message, in order."""
    return [
        json.loads(r.body["messages"][-1]["content"])
        for r in fake.requests
        if r.body.get("response_format", {}).get("json_schema", {}).get("schema", {}).get("title")
        == "RouteResult"
    ]


def done(stream: Stream) -> dict[str, Any]:
    result: dict[str, Any] = stream.only("done")[0].data
    return result


# -- AC1: the conversation lives in the checkpointer, keyed by the session ----------------------------------


def test_a_follow_up_reads_the_earlier_turn(client: TestClient, fake: FakeOpenRouter) -> None:
    session = str(uuid.uuid4())
    script(fake)
    assert outcome(ask(client, asking(session))) == "answered"
    script(fake, route=follow_up())
    stream = ask(client, asking(session, FOLLOW_UP))
    first, second = route_inputs(fake)
    assert first == {"question": QUESTION["q"], "history": [], "earlier": []}
    assert second == {
        "question": FOLLOW_UP,
        "history": [{"question": STANDALONE, "answer": PUBLISHED}],
        "earlier": [],
    }
    assert outcome(stream) == "answered"
    assert done(stream)["notice"] is None


def test_another_session_has_no_history(client: TestClient, fake: FakeOpenRouter) -> None:
    script(fake)
    ask(client, asking(str(uuid.uuid4())))
    script(fake, route=follow_up())
    stream = ask(client, asking(str(uuid.uuid4()), FOLLOW_UP))
    assert route_inputs(fake)[-1]["history"] == []
    assert outcome(stream) == "abstained"
    assert done(stream)["notice"] == "no_context"


# -- AC2: a follow-up without context ----------------------------------------------------------------------


def test_a_follow_up_with_no_history_asks_what_the_visitor_means(
    client: TestClient, fake: FakeOpenRouter
) -> None:
    session = str(uuid.uuid4())
    script(fake, route=follow_up())
    stream = ask(client, asking(session, FOLLOW_UP))
    assert stages(stream) == ["guard", "route"]
    assert not stream.only("retrieval") and not stream.only("token") and not stream.only("claim")
    assert stream.names()[-2:] == ["usage", "done"]
    assert done(stream)["outcome"] == "abstained" and done(stream)["notice"] == "no_context"
    assert ANSWER_A not in fake.models_called() and JUDGE not in fake.models_called()
    script(fake)  # the next question finds nothing of the follow-up: it was not remembered
    ask(client, asking(session))
    assert route_inputs(fake)[-1]["history"] == []


def test_a_conversation_idle_past_the_session_ttl_starts_over(
    client: TestClient, fake: FakeOpenRouter
) -> None:
    session, started = str(uuid.uuid4()), time.time()
    app: Any = client.app
    app.state.clock = lambda: started
    script(fake)
    ask(client, asking(session))
    ttl = app.state.limits.session_ttl
    app.state.clock = lambda: started + ttl - 1  # still within the limit
    script(fake, route=follow_up())
    assert done(ask(client, asking(session, FOLLOW_UP)))["notice"] is None
    app.state.clock = lambda: started + 2 * ttl + 1  # idle past it since the last question
    script(fake, route=follow_up())
    stream = ask(client, asking(session, FOLLOW_UP))
    assert route_inputs(fake)[-1]["history"] == []
    assert done(stream)["outcome"] == "abstained" and done(stream)["notice"] == "no_context"
    script(fake)
    assert outcome(ask(client, asking(session))) == "answered"
    assert route_inputs(fake)[-1]["history"] == []  # a new conversation
    app.state.clock = lambda: started + 2 * ttl + 2
    script(fake, route=follow_up())
    ask(client, asking(session, FOLLOW_UP))
    assert route_inputs(fake)[-1]["history"] == [{"question": STANDALONE, "answer": PUBLISHED}]


def stored(url: str, session: str) -> dict[str, list[Any]]:
    """What the checkpointer holds of the session's thread, table by table."""
    with psycopg.connect(url) as conn:
        return {
            "checkpoints": conn.execute(
                "SELECT checkpoint FROM langgraph.checkpoints WHERE thread_id = %s", (session,)
            ).fetchall(),
            "blobs": conn.execute(
                "SELECT channel, blob FROM langgraph.checkpoint_blobs WHERE thread_id = %s", (session,)
            ).fetchall(),
            "writes": conn.execute(
                "SELECT channel, blob FROM langgraph.checkpoint_writes WHERE thread_id = %s", (session,)
            ).fetchall(),
        }


def test_the_conversation_survives_a_restart(corpus_database: str, fake: FakeOpenRouter) -> None:
    session = str(uuid.uuid4())
    with TestClient(build_app(api_settings(corpus_database), openrouter=fake)) as client:
        script(fake)
        ask(client, asking(session))
    assert stored(corpus_database, session)["checkpoints"]
    with TestClient(build_app(api_settings(corpus_database), openrouter=fake)) as client:
        script(fake, route=follow_up())
        stream = ask(client, asking(session, FOLLOW_UP))
    assert route_inputs(fake)[-1]["history"] == [{"question": STANDALONE, "answer": PUBLISHED}]
    assert outcome(stream) == "answered"


def test_only_the_conversation_is_stored(
    corpus_database: str, client: TestClient, fake: FakeOpenRouter
) -> None:
    session = str(uuid.uuid4())
    script(fake, judge=verdicts((0, "supported"), (1, "unsupported")))  # the second claim is struck out
    stream = ask(client, asking(session))
    assert outcome(stream) == "answered"
    thread = stored(corpus_database, session)
    [(checkpoint,)] = thread["checkpoints"]
    assert checkpoint["channel_values"] == {}  # no step's value inlined
    assert thread["writes"] == []
    [(channel, blob)] = thread["blobs"]
    assert channel == "conversation"
    assert CLAIMS[0][0].encode() in blob and STANDALONE.encode() in blob
    assert CLAIMS[1][0].encode() not in blob  # drafted, never published as checked
    for hit in stream.only("retrieval")[-1].data["hits"]:
        assert hit["title"].encode() not in blob


# -- AC3: a question in a language the domain doesn't declare --------------------------------------------


def answer_language_asked(fake: FakeOpenRouter) -> str:
    """The language the last `answer` call was told to write in."""
    system = [r for r in fake.requests if r.model == ANSWER_A][-1].body["messages"][0]["content"]
    match = re.search(r"Answer in the language `(\w+)`", system)
    assert match
    return match.group(1)


def routed_in(language: str) -> Reply:
    output = {"intent": "about_andre", "standalone_question": STANDALONE, "language": language,
              "follow_up": False, "public_ok": True}  # fmt: skip
    return Reply(json.dumps(output))


def test_another_language_is_answered_in_the_default_language_with_a_notice(
    client: TestClient, fake: FakeOpenRouter
) -> None:
    script(fake, route=routed_in("es"))
    stream = ask(client, asking(str(uuid.uuid4()), "¿Qué servicios de AWS ha usado?"))
    assert answer_language_asked(fake) == "en"  # zorzo's first language
    assert done(stream)["outcome"] == "answered" and done(stream)["notice"] == "other_language"


def test_a_declared_language_has_no_notice(client: TestClient, fake: FakeOpenRouter) -> None:
    script(fake, route=routed_in("pt"))
    stream = ask(client, asking(str(uuid.uuid4()), "Quais serviços da AWS ele usou?"))
    assert answer_language_asked(fake) == "pt"
    assert done(stream)["outcome"] == "answered" and done(stream)["notice"] is None
