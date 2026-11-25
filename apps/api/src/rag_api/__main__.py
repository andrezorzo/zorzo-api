"""Process entry point: validate settings and the domain, then serve. Fails fast before serving."""

import sys

import uvicorn
import yaml
from pydantic import ValidationError
from rag_core.adapters.factory import ConfigError
from rag_core.domain import DomainError

from rag_api.main import create_app
from rag_api.settings import ApiSettings


def main() -> None:
    try:
        settings = ApiSettings()  # type: ignore[call-arg]
        app = create_app(settings)
    except (ValidationError, DomainError, ConfigError, OSError, yaml.YAMLError) as exc:
        print(f"rag-api: refusing to start: {exc}", file=sys.stderr)
        sys.exit(1)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level=settings.log_level.lower())


if __name__ == "__main__":
    main()
