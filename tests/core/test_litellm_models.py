"""F01-T13 AC7: importing the LiteLLM adapter and building the Router opens no connection (no remote cost
map)."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Run in a fresh interpreter: the import must happen with the socket layer already closed.
PROBE = """
import socket, sys
from pathlib import Path

def refuse(*args, **kwargs):
    raise SystemExit(f"network attempt: {args[:1]}")

socket.socket.connect = refuse
socket.socket.connect_ex = refuse
socket.getaddrinfo = refuse
socket.create_connection = refuse

from rag_core.adapters.litellm_models import LiteLLMModels
from rag_core.models_config import load_models

models = load_models(Path(sys.argv[1]) / "config/models.yaml")
LiteLLMModels(models, {"openrouter": "test-key"}, cooldown_s=60)
print("ok")
"""


def test_imports_and_builds_without_network() -> None:
    env = {"PATH": "/usr/bin:/bin", "HOME": "/tmp"}  # no LITELLM_LOCAL_MODEL_COST_MAP: the adapter sets it
    result = subprocess.run(
        [sys.executable, "-c", PROBE, str(ROOT)], capture_output=True, text=True, env=env, timeout=120
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().endswith("ok")
