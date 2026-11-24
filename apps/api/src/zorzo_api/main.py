"""FastAPI app factory: validates settings, loads the domain and reflects the schema at startup."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from zorzo_core.adapters.postgres import Database
from zorzo_core.domain import load_domain
from zorzo_core.telemetry import configure_logging

from zorzo_api import errors, health
from zorzo_api.settings import ApiSettings


def create_app(settings: ApiSettings) -> FastAPI:
    configure_logging(settings.app_env)
    domain = load_domain(settings.domain)
    db = Database(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            await db.reflect()
            yield
        finally:
            await db.close()

    app = FastAPI(title="zorzo-api", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.domain = domain
    app.state.db = db
    errors.install(app)
    app.include_router(health.router)
    return app
