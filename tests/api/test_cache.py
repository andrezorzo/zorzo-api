"""F01-T09 AC1 to AC4: the answer cache on both answer paths, read from `POST /api/v1/ask` as a stream."""

import json
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from structlog.testing import capture_logs

from tests.api.test_answer import (
    CLAIMS,
    STANDALONE,
    answer,
    claims,
    outcome,
    routed,
    script,
    stages,
    verdicts,
)
from tests.api.test_ask import QUESTION, ask
from tests.conftest import (
    TEST_CANARY,
    api_settings,
    build_app,
    reset_tables,
    seed_budget,
    wait_for_cache_writes,
)
from tests.contract.sse import Stream
from tests.fakes.openrouter import FakeOpenRouter, Reply

ROUTE_MODEL = "test/route:free"
DOMAIN_LANGUAGES = {"en", "pt"}
SPANISH = "¿Qué servicios de AWS ha usado él?"
SMALL_BURST = 2


@pytest.fixture
def fake() -> FakeOpenRouter:
    return FakeOpenRouter()


@pytest.fixture
def client(corpus_database: str, fake: FakeOpenRouter) -> Iterator[TestClient]:
    with TestClient(build_app(api_settings(corpus_database), openrouter=fake)) as c:
        yield c


def rows(url: str) -> list[dict[str, Any]]:
    with psycopg.connect(url, row_factory=dict_row) as conn:
        return conn.execute("SELECT * FROM answer_cache ORDER BY id").fetchall()


def ask_and_wait(client: TestClient, body: dict[str, Any] | None = None) -> Stream:
    stream = ask(client, body)
    wait_for_cache_writes(client)
    return stream


def questions_asked(fake: FakeOpenRouter) -> list[str]:
    """The models the fake provider was called with, in order."""
    return [r.model for r in fake.requests if r.path == "/chat/completions"]


def stored_run(client: TestClient, fake: FakeOpenRouter, **script_args: Any) -> Stream:
    script(fake, **script_args)
    return ask_and_wait(client)


def with_standalone(text: str, language: str = "en") -> Reply:
    output = {"intent": "about_andre", "standalone_question": text, "language": language, "follow_up": False,
              "public_ok": True}  # fmt: skip
    return Reply(json.dumps(output))


# -- AC1: lookup, storage and expiry -------------------------------------------------------------------------


def test_an_answered_question_stores_its_claims(
    client: TestClient, fake: FakeOpenRouter, corpus_database: str
) -> None:
    first = stored_run(client, fake)
    [row] = rows(corpus_database)
    assert row["question_scrubbed"] == STANDALONE and row["lang"] == "en"
    assert row["answer"] == {"claims": claims(first)}  # as the stream sent them
    assert row["model_config_version"] == client.app.state.model_config_version  # type: ignore[attr-defined]
    assert row["trace_id"] is None and row["hits"] == 0
    with psycopg.connect(corpus_database) as conn:
        active = conn.execute("SELECT version FROM corpus_versions WHERE status = 'active'").fetchone()
    assert active is not None and row["corpus_version"] == active[0]
    ttl = client.app.state.limits.cache_ttl  # type: ignore[attr-defined]
    assert (row["expires_at"] - row["created_at"]).total_seconds() == pytest.approx(ttl)
    assert stages(first)[2] == "cache"


def test_the_gap_sentence_and_the_verdicts_are_stored_as_sent(
    client: TestClient, fake: FakeOpenRouter, corpus_database: str
) -> None:
    gap = "The sources don't say which team he led."
    first = stored_run(
        client,
        fake,
        draft=answer(CLAIMS, gap=gap),
        judge=verdicts((0, "supported"), (1, "partial", "He built.")),
    )
    stored = rows(corpus_database)[0]["answer"]["claims"]
    assert stored == claims(first)
    assert [c["verdict"] for c in stored] == ["supported", "partial", "gap"] and stored[-1]["text"] == gap


@pytest.mark.parametrize(
    "case",
    ["abstention", "decline", "contact", "block", "degraded", "other_language", "personal_data"],
)
def test_only_verified_answers_are_stored(
    client: TestClient, fake: FakeOpenRouter, corpus_database: str, case: str
) -> None:
    if case == "abstention":
        script(fake, draft=answer([], abstained=True))
    elif case == "decline":
        script(fake, route=routed("off_topic"))
    elif case == "contact":
        script(fake, route=routed("contact", "How can I reach him?"))
    elif case == "block":
        script(fake, draft=answer([(f"The marker is {TEST_CANARY}.", [1])]))
    elif case == "other_language":
        script(fake, route=with_standalone(SPANISH, "es"))
    elif case == "personal_data":
        script(fake, route=with_standalone("What is the email joao.silva@gmail.com used for?"))
    # `degraded`: the fake's default reply isn't route JSON, so the models can't answer.
    stream = ask_and_wait(client)
    assert rows(corpus_database) == []
    if case == "degraded":
        assert stream.only("degraded")
    if case in ("other_language", "personal_data"):
        assert "cache" not in stages(stream) and outcome(stream) == "answered"


def test_a_hit_stores_nothing_more(client: TestClient, fake: FakeOpenRouter, corpus_database: str) -> None:
    stored_run(client, fake)
    script(fake)
    ask_and_wait(client)
    [row] = rows(corpus_database)
    assert row["hits"] == 1


def test_an_almost_identical_question_gets_the_stored_answer(
    client: TestClient, fake: FakeOpenRouter
) -> None:
    first = stored_run(client, fake)
    script(fake, route=with_standalone("which aws SERVICES has andré used"))  # other case, no punctuation
    again = ask_and_wait(client)
    assert again.only("done")[0].data["cached"] is True
    assert claims(again) == claims(first)
    # One more word is below `CACHE_SIMILARITY` with the fake embedder: a miss, the full graph runs.
    script(fake, route=with_standalone(STANDALONE.replace("used?", "used so far?")))
    miss = ask_and_wait(client)
    assert miss.only("done")[0].data["cached"] is False
    assert stages(miss)[2:4] == ["cache", "retrieve"] and miss.only("token")


def test_a_question_in_another_language_skips_the_cache(
    client: TestClient, fake: FakeOpenRouter, corpus_database: str
) -> None:
    stored_run(client, fake)
    script(fake, route=with_standalone(STANDALONE, "es"))  # the same words, a language the domain lacks
    stream = ask_and_wait(client)
    assert "cache" not in stages(stream) and stream.only("token")
    assert [r["hits"] for r in rows(corpus_database)] == [0]


def test_a_question_with_personal_data_skips_the_cache(client: TestClient, fake: FakeOpenRouter) -> None:
    script(fake, route=with_standalone("Which AWS services has joao.silva@gmail.com used?"))
    first = ask_and_wait(client)
    script(fake, route=with_standalone("Which AWS services has joao.silva@gmail.com used?"))
    second = ask_and_wait(client)
    for stream in (first, second):
        assert "cache" not in stages(stream) and stream.only("token")
        assert stream.only("done")[0].data["cached"] is False


def test_a_failing_cache_store_is_a_miss(
    client: TestClient, fake: FakeOpenRouter, corpus_database: str
) -> None:
    stored_run(client, fake)
    cache = client.app.state.answer_cache.store  # type: ignore[attr-defined]

    async def broken(*_: Any, **__: Any) -> Any:
        raise RuntimeError("the cache table is gone, with the question in its message")

    cache.lookup, cache.store = broken, broken
    script(fake)
    with capture_logs() as logs:
        stream = ask_and_wait(client)
    assert outcome(stream) == "answered" and stream.only("token")
    assert stream.only("done")[0].data["cached"] is False
    warnings = [line for line in logs if line["event"] in ("cache_lookup_failed", "cache_write_failed")]
    assert {w["event"] for w in warnings} == {"cache_lookup_failed", "cache_write_failed"}
    assert all(w["log_level"] == "warning" and w["error"] == "RuntimeError" for w in warnings)
    assert "question" not in str(warnings)
    assert len(rows(corpus_database)) == 1  # only the first run's row


# -- AC2: a hit ----------------------------------------------------------------------------------------------


def test_a_hit_replays_the_verified_claims_with_no_token(client: TestClient, fake: FakeOpenRouter) -> None:
    first = stored_run(client, fake)
    before = len(fake.requests)
    script(fake)
    hit = ask_and_wait(client)
    assert stages(hit) == ["guard", "route", "cache"]
    assert [e.data["stage"] for e in hit.only("model")] == ["route"]
    assert claims(hit) == claims(first)
    assert not hit.only("token") and not hit.only("retrieval")
    usage = hit.only("usage")[0].data
    assert (usage["tokens_in"], usage["tokens_out"], usage["cost_usd"]) == (100, 20, 0.0)  # route's call
    done = hit.only("done")[0].data
    assert done["cached"] is True and done["outcome"] == "answered" and done["suggestion"] is None
    assert [o["label"] for o in done["contact"]] == ["LinkedIn", "GitHub", "Email"]
    # Only `route` ran: no `assess`, `answer` or judge call, and no remote rerank.
    new_calls = fake.requests[before:]
    assert [r.path for r in new_calls] == ["/chat/completions"] and new_calls[0].model == ROUTE_MODEL


def test_a_hit_still_counts_against_the_rate_limit(corpus_database: str, fake: FakeOpenRouter) -> None:
    app = build_app(
        api_settings(corpus_database), limits={"rate_session_burst": SMALL_BURST}, openrouter=fake
    )
    body = {**QUESTION, "session": str(uuid.uuid4())}
    with TestClient(app) as client:
        script(fake)
        assert ask_and_wait(client, body).only("done")[0].data["cached"] is False
        script(fake)
        assert ask_and_wait(client, body).only("done")[0].data["cached"] is True
        refused = client.post("/api/v1/ask", json=body)
    assert refused.status_code == 429 and refused.json()["error"]["code"] == "rate_limited"


# -- AC3: degraded mode --------------------------------------------------------------------------------------


@pytest.fixture
def at_the_cap(corpus_database: str) -> Iterator[None]:
    reset_tables(corpus_database, "budget_daily", "alert_states")
    yield
    reset_tables(corpus_database, "budget_daily", "alert_states")


def degraded_ask(client: TestClient, corpus_database: str, question: str) -> Stream:
    seed_budget(corpus_database, 0.10, paid_disabled=True, degraded=True)
    return ask(client, {**QUESTION, "q": question, "session": str(uuid.uuid4())})


def test_degraded_mode_serves_a_first_question_from_the_cache(
    client: TestClient, fake: FakeOpenRouter, corpus_database: str, at_the_cap: None
) -> None:
    script(fake, route=with_standalone(QUESTION["q"]))
    first = ask_and_wait(client)
    before = len(fake.requests)
    stream = degraded_ask(client, corpus_database, QUESTION["q"])
    assert stages(stream) == ["cache"] and not stream.only("degraded")
    assert claims(stream) == claims(first)
    assert stream.only("usage")[0].data["cost_usd"] == 0.0 and not stream.only("token")
    done = stream.only("done")[0].data
    assert done["cached"] is True and done["outcome"] == "answered" and done["suggestion"] is None
    assert len(fake.requests) == before  # no model call at all


@pytest.mark.parametrize(
    ("question", "first_stage"),
    [
        ("What is his favourite colour?", "cache"),  # looked up, nothing stored near it
        (SPANISH, "retrieve"),  # a language the domain doesn't declare: no lookup
        ("Mail joao.silva@gmail.com?", "retrieve"),  # personal data: no lookup
    ],
)
def test_degraded_mode_without_a_match_is_search_only(
    client: TestClient,
    fake: FakeOpenRouter,
    corpus_database: str,
    at_the_cap: None,
    question: str,
    first_stage: str,
) -> None:
    script(fake, route=with_standalone(QUESTION["q"]))
    ask_and_wait(client)
    before = len(fake.requests)
    stream = degraded_ask(client, corpus_database, question)
    assert stream.names()[-3:] == ["degraded", "retrieval", "done"]
    assert stream.only("done")[0].data["cached"] is False
    assert stages(stream)[0] == first_stage
    assert len(fake.requests) == before


def test_degraded_mode_skips_a_language_the_domain_doesnt_declare(
    client: TestClient, corpus_database: str, at_the_cap: None
) -> None:
    stream = degraded_ask(client, corpus_database, SPANISH)
    assert "cache" not in stages(stream) and stream.only("degraded")


# -- review fixes: a replay passes the publish checks ----------------------------------------

BAD_LINK = "https://evil.example/leak"


def poison_stored_claim(url: str, text: str) -> None:
    """Rewrites the stored row's first claim, as a row written before the link allowlist changed would be."""
    with psycopg.connect(url) as conn:
        conn.execute(
            "UPDATE answer_cache SET answer = "
            "jsonb_set(answer, '{claims,0,text}', to_jsonb(CAST(%s AS text)))",
            (text,),
        )


def test_a_stored_claim_linking_outside_the_allowlist_is_a_miss(
    client: TestClient, fake: FakeOpenRouter, corpus_database: str
) -> None:
    stored_run(client, fake)
    poison_stored_claim(corpus_database, f"See {BAD_LINK}")
    script(fake)
    stream = ask_and_wait(client)
    assert BAD_LINK not in str(stream.names()) + str([e.data for e in stream.events])
    assert stream.only("done")[0].data["cached"] is False and stream.only("token")
    [row] = rows(corpus_database)  # the refused row is gone; the new run stored a servable one
    assert BAD_LINK not in json.dumps(row["answer"])


def test_a_stored_claim_with_the_canary_is_a_miss(
    client: TestClient, fake: FakeOpenRouter, corpus_database: str
) -> None:
    stored_run(client, fake)
    poison_stored_claim(corpus_database, f"Secret {TEST_CANARY}")
    script(fake)
    stream = ask_and_wait(client)
    assert TEST_CANARY not in str([e.data for e in stream.events])
    assert stream.only("done")[0].data["cached"] is False


def test_degraded_mode_does_not_serve_a_claim_linking_outside_the_allowlist(
    client: TestClient, fake: FakeOpenRouter, corpus_database: str, at_the_cap: None
) -> None:
    script(fake, route=with_standalone(QUESTION["q"]))
    ask_and_wait(client)
    poison_stored_claim(corpus_database, f"See {BAD_LINK}")
    stream = degraded_ask(client, corpus_database, QUESTION["q"])
    assert BAD_LINK not in str([e.data for e in stream.events])
    assert stream.names()[-3:] == ["degraded", "retrieval", "done"]
    assert stream.only("done")[0].data["cached"] is False
    assert rows(corpus_database) == []  # deleted


def test_a_degraded_miss_embeds_the_question_once(
    client: TestClient, corpus_database: str, at_the_cap: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    embedder = client.app.state.embedder  # type: ignore[attr-defined]
    real = embedder.embed
    calls: list[list[str]] = []

    async def counting(texts: list[str]) -> Any:
        calls.append(list(texts))
        return await real(texts)

    monkeypatch.setattr(embedder, "embed", counting)
    stream = degraded_ask(client, corpus_database, "What is his favourite colour?")
    assert stream.names()[-3:] == ["degraded", "retrieval", "done"]
    assert len(calls) == 1
