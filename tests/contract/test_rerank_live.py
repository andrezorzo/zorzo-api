"""F01-T13 AC4: OpenRouter's real `/rerank` answers through the LiteLLM Router (a Cohere-style call on the
provider's base URL) the shape the stand-in answers, with the production chain's free rerank model.

Marked `network`, and skipped without `OPENROUTER_API_KEY`: one free (`:free`, no cost) call per run.
"""

import os
from pathlib import Path

import pytest
from rag_core.adapters.litellm_models import LiteLLMModels
from rag_core.models_config import entry_id, load_models

pytestmark = pytest.mark.network

ROOT = Path(__file__).resolve().parents[2]
TIMEOUT_S = 10.0


async def test_the_rerank_endpoint_answers_the_expected_shape() -> None:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        pytest.skip("OPENROUTER_API_KEY is not set")
    models = load_models(ROOT / "config/models.yaml")
    [entry] = models.stage("rerank").chain
    assert not entry.paid
    router = LiteLLMModels(models, {entry.provider: key}, cooldown_s=60)
    documents = [
        "A carrot cake recipe with chocolate frosting.",
        "André used AWS Lambda, Kinesis and DynamoDB in distributed systems.",
        "The site answers only from reviewed sources and shows citations.",
    ]
    try:
        question = "Quais serviços da AWS o André usou?"
        scores = await router.rerank(entry_id("rerank", entry), question, documents, timeout=TIMEOUT_S)
    finally:
        await router.close()
    assert len(scores) == 3
    assert max(range(3), key=lambda i: scores[i]) == 1
