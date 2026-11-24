"""`GET /api/healthz`: used by the front, the deploy smoke test and the uptime monitor."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from rag_core.services import health

router = APIRouter()


@router.get("/api/healthz")
async def healthz(request: Request) -> JSONResponse:
    report = await health.check(request.app.state.db)
    return JSONResponse(
        {"status": report.status, "mode": report.mode, "db": report.db},
        status_code=200 if report.healthy else 503,
        headers={"Cache-Control": "no-store"},
    )
