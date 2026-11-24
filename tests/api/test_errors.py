"""AC5: one error body and catalog (LLD "Errors"), reused by every route."""

from collections.abc import Iterator

import pytest
import structlog
from fastapi import APIRouter
from fastapi.testclient import TestClient
from pydantic import BaseModel
from rag_api.main import create_app
from rag_core.errors import ApiError, ErrorCode
from rag_core.telemetry import configure_logging
from structlog.testing import capture_logs

from tests.conftest import api_settings


class Body(BaseModel):
    q: str


probe = APIRouter()


@probe.post("/test/echo")
async def echo(body: Body) -> dict[str, str]:
    return {"q": body.q}


@probe.get("/test/boom")
async def boom() -> None:
    raise RuntimeError("unexpected")


@probe.get("/test/unavailable")
async def unavailable() -> None:
    raise ApiError(ErrorCode.UNAVAILABLE, "The backend can't serve right now.")


@pytest.fixture
def client(database_url: str) -> Iterator[TestClient]:
    app = create_app(api_settings(database_url))
    app.include_router(probe)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _assert_error(response_json: dict[str, dict[str, str]], code: str) -> str:
    error = response_json["error"]
    assert error["code"] == code
    assert error["message"]
    assert error["trace_id"]
    return error["trace_id"]


def test_maps_validation_errors_to_invalid_input(client: TestClient) -> None:
    response = client.post("/test/echo", json={})
    assert response.status_code == 422
    _assert_error(response.json(), "invalid_input")


def test_maps_unexpected_errors_to_internal(client: TestClient) -> None:
    response = client.get("/test/boom")
    assert response.status_code == 500
    _assert_error(response.json(), "internal")


def test_maps_unavailable_to_503(client: TestClient) -> None:
    response = client.get("/test/unavailable")
    assert response.status_code == 503
    _assert_error(response.json(), "unavailable")


def test_error_trace_id_matches_the_log_line(client: TestClient) -> None:
    with capture_logs() as logs:
        response = client.post("/test/echo", json={})
    trace_id = _assert_error(response.json(), "invalid_input")
    request_lines = [line for line in logs if line.get("event") == "request"]
    assert len(request_lines) == 1
    assert request_lines[0]["trace_id"] == trace_id
    assert request_lines[0]["status"] == 422


def test_production_logs_keep_the_traceback_of_an_unhandled_error(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("production")
    try:
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            structlog.get_logger().exception("unhandled_error")
        out = capsys.readouterr().out
    finally:
        configure_logging("local")
    assert "RuntimeError" in out
    assert "boom" in out
