"""Process entry point: validate settings and the domain, then serve. Fails fast before serving."""

import os
import sys

import uvicorn
from pydantic import ValidationError
from zorzo_core.domain import DomainError

from zorzo_api.main import create_app
from zorzo_api.settings import ApiSettings


def main() -> None:
    try:
        settings = ApiSettings()  # type: ignore[call-arg]
        app = create_app(settings)
    except (ValidationError, DomainError) as exc:
        print(f"zorzo-api: refusing to start: {exc}", file=sys.stderr)
        sys.exit(1)
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
