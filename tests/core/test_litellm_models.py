"""F01-T13 AC7: importing the LiteLLM adapter and building the Router opens no connection (no remote cost
map); and the provider's numbers are checked before they settle the budget."""

import subprocess
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

import pytest
from rag_core.adapters.litellm_models import MAX_TOKEN_COUNT, LiteLLMModels, _cost, _counts
from rag_core.models_config import load_models
from rag_core.ports import ProviderError

from tests.conftest import served
from tests.fakes.openrouter import FakeOpenRouter

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


@pytest.mark.parametrize(
    ("reported", "cost"),
    [(0.0004, 0.0004), (0, 0.0), (-0.5, None), (float("inf"), None), (float("nan"), None),
     ("0.1", None), (True, None), (None, None)],
)  # fmt: skip
def test_a_reported_cost_counts_only_when_finite_and_not_negative(
    reported: object, cost: float | None
) -> None:
    assert _cost(SimpleNamespace(cost=reported)) == cost


@pytest.mark.parametrize(
    ("prompt", "completion", "counts"),
    [(7, 2, (7, 2)), (None, 4, (0, 0)), (-1, 4, (0, 0)), (7.0, 2, (0, 0)), (True, 2, (0, 0)),
     (MAX_TOKEN_COUNT + 1, 2, (0, 0))],
)  # fmt: skip
def test_token_counts_count_only_when_both_are_plausible(
    prompt: object, completion: object, counts: tuple[int, int]
) -> None:
    assert _counts(SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion)) == counts


async def test_a_providers_error_body_never_reaches_a_traceback() -> None:
    fake = FakeOpenRouter()
    models = LiteLLMModels(served(load_models(ROOT / "tests/fixtures/models.test.yaml"), fake), {}, 60)
    fake.script("test/answer-a:free", "moderated")
    question = "my phone is 555-0100"  # what a visitor typed, echoed back by the provider
    try:
        await models.complete("answer:openrouter/test/answer-a:free", [{"role": "user", "content": question}],
                              max_tokens=5, schema=None, timeout=5)  # fmt: skip
    except ProviderError as exc:
        rendered = "".join(traceback.format_exception(exc))
    finally:
        await models.close()
    assert "http 403" in rendered
    assert question not in rendered  # the library's error, which quotes it, stays out of the chain
