"""F01-T06 AC2 to AC6 and AC8: verified answers with citations, read from `POST /api/v1/ask` as a stream.

The fake provider answers each step: `route`, `assess` and `reformulate` share the route model and are told
apart by their output schema; the answer streams its JSON; the judge returns verdicts.
"""

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.test_ask import QUESTION, ask
from tests.conftest import api_settings, build_app
from tests.contract.sse import Stream
from tests.fakes.openrouter import FakeOpenRouter, Reply

ANSWER_A, ANSWER_B, JUDGE = "test/answer-a:free", "test/answer-b:free", "test/judge:free"
STANDALONE = "Which AWS services has André used?"
CLAIMS = [("André used Lambda and Kinesis on AWS.", [1]), ("He built serverless pipelines.", [1, 2])]


def routed(intent: str = "about_andre", standalone: str = STANDALONE) -> Reply:
    output = {"intent": intent, "standalone_question": standalone, "language": "en", "follow_up": False,
              "public_ok": True}  # fmt: skip
    return Reply(json.dumps(output))


def answer(claims: list[tuple[str, list[int]]], gap: str | None = None, abstained: bool = False) -> Reply:
    body = {"answer_lang": "en", "claims": [{"text": t, "sources": s} for t, s in claims], "gap": gap,
            "abstained": abstained}  # fmt: skip
    return Reply(json.dumps(body))


def verdicts(*items: tuple[int, str] | tuple[int, str, str]) -> Reply:
    rows = [{"i": v[0], "verdict": v[1], "supported_text": v[2] if len(v) > 2 else None} for v in items]
    return Reply(json.dumps({"verdicts": rows}))


def script(
    fake: FakeOpenRouter,
    *,
    route: Reply | None = None,
    sufficient: bool = True,
    draft: Reply | str | None = None,
    judge: Reply | str | None = None,
) -> None:
    fake.script_schema("RouteResult", route or routed())
    fake.script_schema("AssessResult", Reply(json.dumps({"sufficient": sufficient})))
    fake.script(ANSWER_A, draft or answer(CLAIMS))
    fake.script(JUDGE, judge or verdicts((0, "supported"), (1, "supported")))


@pytest.fixture
def fake() -> FakeOpenRouter:
    return FakeOpenRouter()


@pytest.fixture
def client(corpus_database: str, fake: FakeOpenRouter) -> Iterator[TestClient]:
    with TestClient(build_app(api_settings(corpus_database), openrouter=fake)) as c:
        yield c


def stages(stream: Stream) -> list[str]:
    return [e.data["name"] for e in stream.only("stage") if e.data["status"] == "start"]


def passage_ids(stream: Stream) -> list[int]:
    return [h["id"] for h in stream.only("retrieval")[-1].data["hits"]]


def claims(stream: Stream) -> list[dict[str, Any]]:
    return [e.data for e in stream.only("claim")]


def outcome(stream: Stream) -> str:
    result: str = stream.only("done")[0].data["outcome"]
    return result


# -- AC4: the draft, then the checked claims ---------------------------------------------------------------


def test_the_draft_streams_before_the_checked_claims(client: TestClient, fake: FakeOpenRouter) -> None:
    script(fake)
    stream = ask(client)
    names = stream.names()
    assert names.index("retrieval") < names.index("token") < names.index("claim")
    assert names[-2:] == ["usage", "done"]
    draft = "".join(e.data["text"] for e in stream.only("token"))
    assert draft == "André used Lambda and Kinesis on AWS. He built serverless pipelines."
    ids = passage_ids(stream)
    assert claims(stream) == [
        {"i": 0, "text": CLAIMS[0][0], "sources": [ids[0]], "verdict": "supported"},
        {"i": 1, "text": CLAIMS[1][0], "sources": [ids[0], ids[1]], "verdict": "supported"},
    ]
    assert [e.data["stage"] for e in stream.only("model")] == ["route", "assess", "answer", "verify"]
    usage = stream.only("usage")[0].data
    assert (usage["tokens_in"], usage["tokens_out"], usage["cost_usd"]) == (400, 80, 0.0)
    assert outcome(stream) == "answered"


def test_verification_out_of_time_abstains_after_the_draft(
    corpus_database: str, fake: FakeOpenRouter
) -> None:
    script(fake, judge="hang")
    limits: dict[str, object] = {"request_deadline": 2.5, "answer_reserve": 0.5, "ping_interval": 0.2}
    with TestClient(build_app(api_settings(corpus_database), limits=limits, openrouter=fake)) as client:
        stream = ask(client)
    assert stream.only("token") and not stream.only("claim")
    assert stream.names()[-2:] == ["usage", "done"]
    assert outcome(stream) == "abstained"


# -- AC2: the bounded steps --------------------------------------------------------------------------------


def test_a_covered_question_runs_the_bounded_steps_in_order(client: TestClient, fake: FakeOpenRouter) -> None:
    script(fake)
    stream = ask(client)
    assert stages(stream) == ["route", "retrieve", "rerank", "assess", "answer", "verify"]
    starts = [(e.data["name"], e.data["status"]) for e in stream.only("stage")]
    assert all((name, "end") in starts for name in stages(stream))


def test_weak_evidence_gets_one_reformulate(client: TestClient, fake: FakeOpenRouter) -> None:
    script(fake, sufficient=False)
    fake.script_schema("ReformulateResult", Reply(json.dumps({"query": "AWS Lambda Kinesis serverless"})))
    stream = ask(client)
    assert stages(stream) == [
        "route", "retrieve", "rerank", "assess", "reformulate", "retrieve", "rerank", "answer", "verify",
    ]  # fmt: skip
    searched = [r.body for r in fake.requests if r.path == "/rerank"]
    assert searched[-1]["query"] == "AWS Lambda Kinesis serverless"
    assert outcome(stream) == "answered"


def test_a_failed_verification_gets_one_repair_then_abstains(
    client: TestClient, fake: FakeOpenRouter
) -> None:
    script(fake)
    fake.script_schema(
        "Verdicts", verdicts((0, "unsupported"), (1, "unsupported")), verdicts((0, "unsupported"))
    )
    fake.script_schema("AnswerContract", answer([("André used AWS.", [1])]))
    stream = ask(client)
    assert stages(stream)[-3:] == ["verify", "repair", "verify"]
    assert not stream.only("claim")
    assert outcome(stream) == "abstained"


def test_a_repaired_answer_publishes_its_own_claims(client: TestClient, fake: FakeOpenRouter) -> None:
    script(fake)
    fake.script_schema(
        "Verdicts", verdicts((0, "unsupported"), (1, "unsupported")), verdicts((0, "supported"))
    )
    fake.script_schema("AnswerContract", answer([("André used AWS.", [1])]))
    stream = ask(client)
    assert [c["text"] for c in claims(stream)] == ["André used AWS."]
    assert outcome(stream) == "answered"


def test_too_little_time_for_the_answer_goes_search_only(corpus_database: str, fake: FakeOpenRouter) -> None:
    script(fake)
    with TestClient(
        build_app(api_settings(corpus_database), limits={"answer_reserve": 60}, openrouter=fake)
    ) as c:
        stream = ask(c)
    assert stream.only("degraded")[0].data == {"mode": "search_only", "reason": "deadline"}
    assert stream.names()[-3:] == ["degraded", "retrieval", "done"]
    assert stream.only("retrieval")[-1].data["hits"]
    assert outcome(stream) == "degraded"
    assert ANSWER_A not in fake.models_called()


def test_route_unavailable_goes_search_only(client: TestClient, fake: FakeOpenRouter) -> None:
    fake.always("test/route:free", "error")
    stream = ask(client)
    assert stream.only("degraded")[0].data["reason"] == "models_unavailable"
    assert stream.only("retrieval")[-1].data["hits"]  # the raw question's passages
    assert outcome(stream) == "degraded"


# -- AC3: per-claim verification ---------------------------------------------------------------------------


def test_claims_carry_their_sources_and_verdicts(client: TestClient, fake: FakeOpenRouter) -> None:
    script(fake, judge=verdicts((0, "supported"), (1, "partial", "He built pipelines.")))
    stream = ask(client)
    assert [(c["text"], c["verdict"]) for c in claims(stream)] == [
        (CLAIMS[0][0], "supported"),
        ("He built pipelines.", "partial"),
    ]


def test_a_citation_outside_the_requests_passages_removes_the_claim(
    client: TestClient, fake: FakeOpenRouter
) -> None:
    script(fake, draft=answer([(CLAIMS[0][0], [1]), ("He invented the cloud.", [99])]),
           judge=verdicts((0, "supported")))  # fmt: skip
    stream = ask(client)
    assert [(c["text"], c["sources"], c["verdict"]) for c in claims(stream)][1] == (
        "He invented the cloud.", [], "unsupported",
    )  # fmt: skip
    [judged] = [r.body for r in fake.requests if r.model == JUDGE]
    assert "He invented the cloud." not in judged["messages"][0]["content"]


def test_an_unreadable_draft_is_asked_again_with_the_schema(client: TestClient, fake: FakeOpenRouter) -> None:
    script(fake, draft=Reply("Sure, André used Lambda."))
    fake.script_schema("AnswerContract", answer(CLAIMS))
    stream = ask(client)
    assert not stream.only("token")  # nothing readable as claims was streamed
    assert [c["text"] for c in claims(stream)] == [CLAIMS[0][0], CLAIMS[1][0]]
    assert outcome(stream) == "answered"


def test_a_judge_that_cant_run_turns_the_answer_into_an_abstention(
    client: TestClient, fake: FakeOpenRouter
) -> None:
    script(fake, judge="error")
    fake.always(JUDGE, "error")
    stream = ask(client)
    assert stream.only("token") and not stream.only("claim")
    assert outcome(stream) == "abstained"


# -- AC5: a partly covered question ------------------------------------------------------------------------


def test_a_partly_covered_question_names_the_missing_part(client: TestClient, fake: FakeOpenRouter) -> None:
    gap = "His certifications are not in my sources."
    script(fake, draft=answer(CLAIMS, gap=gap))
    stream = ask(client)
    assert claims(stream)[-1] == {"i": 2, "text": gap, "sources": [], "verdict": "gap"}
    assert outcome(stream) == "answered"


# -- AC6: career facts only from the FAQ -------------------------------------------------------------------


def test_career_facts_are_answered_only_from_the_faq(client: TestClient, fake: FakeOpenRouter) -> None:
    script(fake, route=routed("career_fact", "Does he work remotely in United States business hours?"))
    stream = ask(client, {**QUESTION, "q": "Does he work in US hours?"})
    hits = stream.only("retrieval")[-1].data["hits"]
    assert hits and {h["title"] for h in hits} == {"FAQ"}
    assert outcome(stream) == "answered"


def test_a_career_fact_the_faq_lacks_abstains_without_an_answer_call(
    client: TestClient, fake: FakeOpenRouter
) -> None:
    script(fake, route=routed("career_fact", "reciprocal rank fusion cross-encoder"))
    stream = ask(client, {**QUESTION, "q": "What is his salary?"})
    assert stream.only("retrieval")[-1].data["hits"] == []
    assert not {ANSWER_A, ANSWER_B, JUDGE} & set(fake.models_called())
    assert outcome(stream) == "abstained"


# -- AC8: abstention and decline are distinct --------------------------------------------------------------


def test_abstention_and_decline_are_distinct_outcomes(client: TestClient, fake: FakeOpenRouter) -> None:
    script(fake, judge=verdicts((0, "unsupported"), (1, "unsupported")))
    fake.script_schema("AnswerContract", answer([], abstained=True))
    assert outcome(ask(client)) == "abstained"

    fake.script_schema("RouteResult", routed("off_topic"))
    calls = len(fake.requests)
    declined = ask(client, {**QUESTION, "q": "Write me a poem about cats."})
    assert outcome(declined) == "declined"
    assert not {ANSWER_A, JUDGE} & {r.model for r in fake.requests[calls:]}

    script(fake)
    assert outcome(ask(client)) == "answered"


def test_a_fenced_draft_still_streams_its_claims(client: TestClient, fake: FakeOpenRouter) -> None:
    script(fake, draft=Reply("```json\n" + answer(CLAIMS).text + "\n```"))
    stream = ask(client)
    draft = "".join(e.data["text"] for e in stream.only("token"))
    assert draft == "André used Lambda and Kinesis on AWS. He built serverless pipelines."
    assert outcome(stream) == "answered"


def test_steps_before_the_answer_keep_its_reserve(corpus_database: str, fake: FakeOpenRouter) -> None:
    script(fake)
    fake.script_schema("AssessResult", "hang")
    fake.schema_scripts["AssessResult"].rotate(-1)  # the hang first, before the scripted reply
    limits: dict[str, object] = {"request_deadline": 4, "answer_reserve": 3, "ping_interval": 0.2}
    with TestClient(build_app(api_settings(corpus_database), limits=limits, openrouter=fake)) as c:
        stream = ask(c)
    assert stream.only("degraded")[0].data["reason"] == "deadline"
    assert stream.only("retrieval")[-1].data["hits"]  # cut with a second left: the passages still show
    assert ANSWER_A not in fake.models_called()
