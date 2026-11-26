"""AC7: the models config loads into a validated model, and `model_config_version` follows its content."""

from pathlib import Path

import pytest
from pydantic import ValidationError
from rag_core.limits import load_limits
from rag_core.models_config import config_version, load_models

ROOT = Path(__file__).resolve().parents[2]
LIMITS = load_limits(ROOT / "config/limits.yaml")

VALID = """
providers:
  openrouter: {base_url: "https://openrouter.ai/api/v1", key_env: OPENROUTER_API_KEY}
stages:
  route: {max_tokens: 100, chain: [{provider: openrouter, model: "a/route:free"}]}
  rerank: {max_tokens: 1, chain: []}
  answer:
    max_tokens: 700
    chain:
      - {provider: openrouter, model: "a/answer:free"}
      - {provider: openrouter, model: "a/answer", paid: true,
         price: {input_per_mtok: 0.1, output_per_mtok: 0.4}}
  judge: {max_tokens: 100, chain: [{provider: openrouter, model: "a/judge:free"}]}
"""


PAID = (
    '      - {provider: openrouter, model: "a/answer", paid: true,\n'
    "         price: {input_per_mtok: 0.1, output_per_mtok: 0.4}}"
)
FREE = '      - {provider: openrouter, model: "a/answer:free"}'
FREE_THEN_PAID = f"{FREE}\n{PAID}"
PAID_THEN_FREE = f"{PAID}\n{FREE}"


def write(tmp_path: Path, text: str, name: str = "models.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


def test_loads_stages_chains_and_prices(tmp_path: Path) -> None:
    models = load_models(write(tmp_path, VALID))
    assert models.providers["openrouter"].kind == "openrouter"  # from its base URL
    answer = models.stage("answer")
    assert answer.max_tokens == 700
    assert [e.model for e in answer.free] == ["a/answer:free"]
    assert answer.paid is not None and answer.paid.model == "a/answer"
    assert [e.model for e in answer.chain] == ["a/answer:free", "a/answer"]
    paid = answer.chain[1]
    assert paid.paid and paid.price is not None and paid.price.output_per_mtok == 0.4
    assert models.stage("rerank").chain == []


def test_the_shipped_files_load() -> None:
    for name in ("models.yaml", "models.local.yaml"):
        load_models(ROOT / "config" / name)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("  judge:", "  verify:"),  # an unknown stage (and judge missing)
        (",\n         price: {input_per_mtok: 0.1, output_per_mtok: 0.4}", ""),  # a paid entry without prices
        ('base_url: "https://openrouter.ai/api/v1", ', ""),  # a provider without a base URL
        ('provider: openrouter, model: "a/judge:free"', 'provider: groq, model: "a/judge"'),  # unknown
        (
            'route: {max_tokens: 100, chain: [{provider: openrouter, model: "a/route:free"}]}',
            "route: {max_tokens: 100, chain: []}",
        ),  # only rerank may be empty
        (
            FREE_THEN_PAID,
            PAID_THEN_FREE,
        ),  # the paid entry must come last: the free ones run on the Router first
        (
            "rerank: {max_tokens: 1, chain: []}",
            'rerank: {max_tokens: 1, chain: [{provider: openrouter, model: "a/rr", paid: true,'
            " price: {input_per_mtok: 0.1, output_per_mtok: 0}}]}",
        ),  # no paid rerank
    ],
)
def test_rejects_an_invalid_file(tmp_path: Path, old: str, new: str) -> None:
    assert old in VALID
    with pytest.raises(ValidationError):
        load_models(write(tmp_path, VALID.replace(old, new)))


def test_version_follows_content_not_formatting(tmp_path: Path) -> None:
    base = config_version(load_models(write(tmp_path, VALID)), LIMITS)
    reformatted = "# a comment\n" + VALID.replace("{max_tokens: 1, chain: []}", "{chain: [], max_tokens: 1}")
    assert config_version(load_models(write(tmp_path, reformatted)), LIMITS) == base
    repriced = VALID.replace("output_per_mtok: 0.4", "output_per_mtok: 0.5")
    assert config_version(load_models(write(tmp_path, repriced)), LIMITS) != base
    assert (
        config_version(load_models(write(tmp_path, VALID)), LIMITS.model_copy(update={"rrf_k": 61})) != base
    )
