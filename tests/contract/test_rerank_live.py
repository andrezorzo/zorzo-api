"""AC5 and AC6: OpenRouter's real `/rerank` takes the body the adapter sends and answers the shape the
stand-in answers, with the production chain's free rerank model.

Marked `network`, and skipped without `OPENROUTER_API_KEY`: one free (`:free`, no cost) call per run.
"""

import os
from pathlib import Path

import pytest
from rag_core.adapters.openai_compat import OpenAICompatProvider
from rag_core.models_config import load_models

pytestmark = pytest.mark.network

ROOT = Path(__file__).resolve().parents[2]


async def test_the_rerank_endpoint_answers_the_expected_shape() -> None:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        pytest.skip("OPENROUTER_API_KEY is not set")
    models = load_models(ROOT / "config/models.yaml")
    [entry] = models.stage("rerank").chain
    assert not entry.paid
    provider = OpenAICompatProvider(models.providers[entry.provider].base_url, key)
    documents = [
        "A carrot cake recipe with chocolate frosting.",
        "André used AWS Lambda, Kinesis and DynamoDB in distributed systems.",
        "The site answers only from reviewed sources and shows citations.",
    ]
    try:
        result = await provider.rerank(entry.model, "Quais serviços da AWS o André usou?", documents)
    finally:
        await provider.close()
    assert len(result.scores) == 3
    assert max(range(3), key=lambda i: result.scores[i]) == 1
