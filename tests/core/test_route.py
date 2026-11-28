"""F01-T06 AC1: `route` returns the intent, the standalone question, the language, `follow_up` and
`public_ok` in one structured-output call."""

import asyncio
import json
from collections.abc import AsyncIterator

import pytest
from rag_core.adapters.postgres import Database
from rag_core.domain import load_domain, load_intents, load_prompts
from rag_core.services.conversation import Conversation
from rag_core.services.gateway import ModelsUnavailableError, RequestSpend
from rag_core.services.route import route, route_schema

from tests.conftest import reset_tables
from tests.core.test_gateway import LIMITS, make_gateway
from tests.fakes.alerter import RecordingAlerter
from tests.fakes.openrouter import FakeOpenRouter, Reply

ROUTE = "test/route:free"
QUESTION = "Which AWS services has he used?"
OUTPUT = {
    "intent": "about_andre",
    "standalone_question": "Which AWS services has André used?",
    "language": "en",
    "follow_up": False,
    "public_ok": True,
}
INTENTS = load_intents("zorzo", load_domain("zorzo").document_kinds)
PROMPTS = load_prompts("zorzo", INTENTS)
SCHEMA = route_schema(tuple(INTENTS))


@pytest.fixture
async def setup(database_url: str) -> AsyncIterator[tuple[FakeOpenRouter, Database]]:
    reset_tables(database_url, "budget_daily", "alert_states", "log_events")
    db = Database(database_url)
    yield FakeOpenRouter(), db
    await db.close()


async def run(fake: FakeOpenRouter, db: Database) -> object:
    gateway = make_gateway(db, fake, RecordingAlerter())
    try:
        deadline_at = asyncio.get_running_loop().time() + 10
        result, _ = await route(
            QUESTION,
            Conversation(),
            limits=LIMITS,
            gateway=gateway,
            prompt=PROMPTS.route,
            schema=SCHEMA,
            deadline_at=deadline_at,
            spend=RequestSpend(LIMITS.request_cost_cap_usd),
        )
        return result
    finally:
        await gateway.close()


async def test_route_returns_the_five_fields_in_one_call(setup: tuple[FakeOpenRouter, Database]) -> None:
    fake, db = setup
    fake.script(ROUTE, Reply(json.dumps(OUTPUT)))
    result = await run(fake, db)
    assert result.model_dump() == OUTPUT  # type: ignore[attr-defined]
    [request] = fake.requests
    assert request.model == ROUTE
    assert request.body["response_format"]["type"] == "json_schema"
    # A new conversation: the question with an empty history, as JSON (F01-T07).
    assert json.loads(request.body["messages"][-1]["content"]) == {
        "question": QUESTION,
        "history": [],
        "earlier": [],
    }


async def test_route_reads_fenced_json(setup: tuple[FakeOpenRouter, Database]) -> None:
    fake, db = setup
    fake.script(ROUTE, Reply("Here it is:\n```json\n" + json.dumps(OUTPUT) + "\n```"))
    result = await run(fake, db)
    assert result.model_dump() == OUTPUT  # type: ignore[attr-defined]


async def test_an_unknown_intent_is_a_schema_miss(setup: tuple[FakeOpenRouter, Database]) -> None:
    fake, db = setup
    fake.always(ROUTE, Reply(json.dumps({**OUTPUT, "intent": "weather"})))
    with pytest.raises(ModelsUnavailableError):
        await run(fake, db)
    assert len(fake.requests) == 2  # the miss gets one more try on the same entry; the chain has no other
