"""AC1 to AC4 and AC6: the gate on `POST /api/v1/ask`, before the stream opens.

Rules: LLD "Security" and "6.7 Rate limit".

Turnstile's `siteverify` is the stand-in in `tests/fakes/turnstile.py`, answering as the test secrets do.
Every test uses its own IPs and sessions, since the database (and its buckets) is shared by the session.
"""

import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from rag_core.adapters.postgres import PROBE_TIMEOUT_S
from structlog.testing import capture_logs

from tests.conftest import api_settings, build_app
from tests.fakes.turnstile import DUMMY_TOKEN, FAIL_SECRET, FakeSiteverify

NOON = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
SMALL = {"rate_ip_burst": 2, "rate_ip_window": 600, "rate_session_burst": 2}  # one token every 300 s


def new_ip() -> str:
    n = uuid.uuid4().int
    return f"198.{n % 256}.{(n >> 8) % 256}.{(n >> 16) % 256}"


def asking(ip: str | None = None, session: uuid.UUID | None = None, **body: Any) -> dict[str, Any]:
    headers = {"CloudFront-Viewer-Address": f"{ip or new_ip()}:4431"}
    payload = {"q": "Which AWS services has he used?", "session": str(session or uuid.uuid4())}
    return {"headers": headers, "json": {**payload, "turnstile": DUMMY_TOKEN, **body}}


class Api:
    def __init__(self, app: FastAPI, client: TestClient, siteverify: FakeSiteverify) -> None:
        self.app, self.client, self.siteverify = app, client, siteverify

    def ask(self, **kwargs: Any) -> Any:  # the test client's response
        return self.client.post("/api/v1/ask", **kwargs)

    def at(self, moment: datetime) -> None:
        self.app.state.gate.clock = lambda: moment


@pytest.fixture
def make_api(corpus_database: str) -> Iterator[Any]:
    clients: list[TestClient] = []

    def make(limits: dict[str, object] | None = None, **settings: Any) -> Api:
        siteverify = FakeSiteverify()
        app = build_app(api_settings(corpus_database, **settings), siteverify, limits)
        app.state.gate.clock = lambda: NOON
        client = TestClient(app).__enter__()
        clients.append(client)
        return Api(app, client, siteverify)

    yield make
    for client in clients:
        client.__exit__(None, None, None)


def error_code(response: Any) -> str:
    body = response.json()
    assert body["error"]["trace_id"]
    return str(body["error"]["code"])


def bucket_keys(url: str, *parts: str) -> list[str]:
    with psycopg.connect(url) as conn:
        rows = conn.execute("SELECT key FROM rate_buckets").fetchall()
    return [key for (key,) in rows if any(p in key for p in parts)]


# AC1: the bot check


def test_passes_with_a_valid_token(make_api: Any) -> None:
    api = make_api()
    response = api.ask(**asking())
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert api.siteverify.calls == 1


def test_refuses_a_token_the_provider_rejects(make_api: Any) -> None:
    api = make_api(turnstile_secret=FAIL_SECRET)
    response = api.ask(**asking())
    assert response.status_code == 403
    assert error_code(response) == "bot_check_failed"


@pytest.mark.parametrize("mode", ["error", "garbage", "hang"])
def test_refuses_when_the_provider_fails(make_api: Any, mode: str) -> None:
    api = make_api(limits={"timeout_turnstile": 0.2})
    api.siteverify.mode = mode
    with capture_logs() as logs:
        response = api.ask(**asking())
    assert response.status_code == 403
    assert error_code(response) == "bot_check_failed"
    assert any(line["event"] == "bot_check_provider_error" for line in logs)


def test_checks_the_token_before_anything_else(make_api: Any, corpus_database: str) -> None:
    api = make_api(turnstile_secret=FAIL_SECRET)
    session = uuid.uuid4()
    # Refused by the bot check even with a question over the cap, and before any bucket is touched.
    response = api.ask(**asking(session=session, q="a" * 600))
    assert response.status_code == 403
    assert bucket_keys(corpus_database, str(session)) == []
    # Only the body's shape comes first: without a token there is nothing to verify.
    assert api.ask(**asking(turnstile="")).status_code == 422
    assert api.siteverify.calls == 1


# AC2: the question's cap, after normalization


def test_refuses_a_question_over_the_cap(make_api: Any) -> None:
    api = make_api()
    cap = api.app.state.limits.max_question_chars
    assert api.ask(**asking(q="a" * cap)).status_code == 200
    for refused in ("a" * (cap + 1), "​ \x00\t"):
        response = api.ask(**asking(q=refused))
        assert response.status_code == 422
        assert error_code(response) == "invalid_input"


# AC3: the rate limit


def test_limits_questions_per_ip(make_api: Any) -> None:
    api, ip = make_api(limits=SMALL), new_ip()
    assert [api.ask(**asking(ip=ip)).status_code for _ in range(2)] == [200, 200]
    response = api.ask(**asking(ip=ip))  # a new session: the IP's bucket refuses
    assert response.status_code == 429
    assert error_code(response) == "rate_limited"
    assert response.headers["Retry-After"] == "300"
    api.at(NOON + timedelta(seconds=300))
    assert api.ask(**asking(ip=ip)).status_code == 200


def test_limits_questions_per_session(make_api: Any) -> None:
    api, session = make_api(limits=SMALL), uuid.uuid4()
    assert [api.ask(**asking(session=session)).status_code for _ in range(2)] == [200, 200]
    response = api.ask(**asking(session=session))  # a new IP: the session's bucket refuses
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "300"


def test_says_when_the_daily_limit_resets(make_api: Any) -> None:
    api, ip = make_api(limits={"rate_ip_daily": 1}), new_ip()
    api.at(datetime(2026, 11, 25, 23, 0, tzinfo=UTC))
    assert api.ask(**asking(ip=ip)).status_code == 200
    response = api.ask(**asking(ip=ip))
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "3600"  # until midnight UTC
    api.at(datetime(2026, 11, 26, 0, 0, tzinfo=UTC))
    assert api.ask(**asking(ip=ip)).status_code == 200


def test_a_refused_question_takes_no_token(make_api: Any) -> None:
    api, ip, busy = make_api(limits={**SMALL, "rate_session_burst": 1}), new_ip(), uuid.uuid4()
    assert api.ask(**asking(ip=ip, session=busy)).status_code == 200
    assert api.ask(**asking(ip=ip, session=busy)).status_code == 429  # the session refuses…
    assert api.ask(**asking(ip=ip)).status_code == 200  # …so the IP still had its second token
    assert api.ask(**asking(ip=ip)).status_code == 429


# AC4: the raw IP is never stored or logged


def test_never_stores_or_logs_the_raw_ip(make_api: Any, corpus_database: str) -> None:
    api, ip, session = make_api(), "203.0.113.7", uuid.uuid4()
    with capture_logs() as logs:
        assert api.ask(**asking(ip=ip, session=session)).status_code == 200
        api.siteverify.mode = "error"
        assert api.ask(**asking(ip=ip, session=session)).status_code == 403
    assert logs and ip not in repr(logs)
    keys = bucket_keys(corpus_database, "ip:")
    assert keys and not any(ip in key for key in keys)
    today = set(bucket_keys(corpus_database, "ip:"))
    api.siteverify.mode = "ok"
    api.at(NOON + timedelta(days=1))
    assert api.ask(**asking(ip=ip)).status_code == 200
    tomorrow = set(bucket_keys(corpus_database, "ip:")) - today
    assert len(tomorrow) == 2  # a new hash for the same IP: burst and day buckets under new keys


def test_refuses_without_a_viewer_address_in_production(make_api: Any) -> None:
    api = make_api()
    api.app.state.gate.app_env = "production"  # the app itself needs APP_ENV=local for the fake embedder
    request = asking()
    del request["headers"]["CloudFront-Viewer-Address"]
    with capture_logs() as logs:
        response = api.ask(**request)
    assert response.status_code == 503
    assert error_code(response) == "unavailable"
    assert any(line["event"] == "viewer_address_missing" and line["log_level"] == "error" for line in logs)


def test_a_stuck_database_is_unavailable_in_seconds(make_api: Any, corpus_database: str) -> None:
    api, session = make_api(), uuid.uuid4()
    # Another transaction holds this session's bucket row: the take waits on the lock until Postgres ends it.
    with psycopg.connect(corpus_database) as conn:
        conn.execute(
            "INSERT INTO rate_buckets (key, tokens, refilled_at) VALUES (%s, 1, now())",
            (f"session:{session}:burst",),
        )
        conn.execute("SELECT 1 FROM rate_buckets WHERE key = %s FOR UPDATE", (f"session:{session}:burst",))
        started = time.monotonic()
        response = api.ask(**asking(session=session))
        elapsed = time.monotonic() - started
    assert response.status_code == 503
    assert error_code(response) == "unavailable"
    assert elapsed < 5  # bounded by the limiter's own timeout, not the 20 s request deadline


def test_the_gate_never_outlives_the_request_deadline(make_api: Any, corpus_database: str) -> None:
    # The bot check passes after the request's deadline: the rate limit is never tried, and no token is taken.
    api, session = make_api(limits={"request_deadline": 0.2}), uuid.uuid4()
    api.siteverify.mode = "slow"
    with capture_logs() as logs:
        response = api.ask(**asking(session=session))
    assert response.status_code == 503
    assert error_code(response) == "unavailable"
    assert any(
        line["event"] == "deadline_exceeded"
        and line["stage"] == "bot_check"
        and line["log_level"] == "warning"
        for line in logs
    )
    assert bucket_keys(corpus_database, str(session)) == []


def test_a_take_cut_by_the_deadline_takes_nothing(make_api: Any, corpus_database: str) -> None:
    # The deadline must cut the take before Postgres ends the lock wait itself.
    api, session = make_api(limits={"request_deadline": PROBE_TIMEOUT_S / 2}), uuid.uuid4()
    key = f"session:{session}:burst"
    with psycopg.connect(corpus_database) as conn:  # holds the row past the deadline, below the lock timeout
        conn.execute("INSERT INTO rate_buckets (key, tokens, refilled_at) VALUES (%s, 5, now())", (key,))
        conn.execute("SELECT 1 FROM rate_buckets WHERE key = %s FOR UPDATE", (key,))
        with capture_logs() as logs:
            response = api.ask(**asking(session=session))
        conn.rollback()
    assert response.status_code == 503
    assert any(
        line["event"] == "deadline_exceeded"
        and line["stage"] == "rate_limit"
        and line["log_level"] == "error"
        for line in logs
    )
    assert bucket_keys(corpus_database, str(session)) == []  # the held insert rolled back; nothing taken
    assert api.ask(**asking(session=session)).status_code == 200  # and the next question is served
