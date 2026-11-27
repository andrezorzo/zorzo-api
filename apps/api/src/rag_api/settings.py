"""API settings, validated at startup (LLD "Configuration")."""

import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from rag_core.settings import AppSettings

PUBLISHED_LOCAL_SEED = "local-salt-seed-change-me-0123456789"  # infra/.env.example, apps/api/AGENTS.md
        if self.canary.get_secret_value() == PUBLISHED_LOCAL_CANARY:
            raise ValueError("CANARY is the published local example; production needs its own secret")
