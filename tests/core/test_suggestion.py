"""F01-T08 AC3: the closest example question, by embedding similarity, in the run's language."""

import asyncio

import pytest
from rag_core.adapters.fake_embedder import HashEmbedder
from rag_core.domain import Example
from rag_core.services.suggestion import Suggestions

EXAMPLES = [
    Example(id="en-aws", lang="en", text="Which AWS services has he used in production?"),
    Example(id="en-site", lang="en", text="How does this site check its answers against sources?"),
    Example(id="pt-aws", lang="pt", text="Quais serviços da AWS ele já usou em produção?"),
    Example(id="pt-site", lang="pt", text="Como este site confere as respostas nas fontes?"),
]


def far() -> float:
    return asyncio.get_running_loop().time() + 5


async def test_the_suggestion_is_in_the_runs_language() -> None:
    suggestions = Suggestions(EXAMPLES, HashEmbedder())
    question = "Quais serviços da AWS ele usou?"
    assert await suggestions.closest(question, "pt", far()) == "pt-aws"
    assert await suggestions.closest("Which AWS services did he use?", "en", far()) == "en-aws"
    # The same question asked for the other language gets that language's closest example.
    assert await suggestions.closest(question, "en", far()) == "en-aws"
    assert await suggestions.closest("How does the site check answers?", "pt", far()) == "pt-site"


async def test_a_language_with_no_examples_has_no_suggestion() -> None:
    assert await Suggestions(EXAMPLES, HashEmbedder()).closest("q", "fr", far()) is None


async def test_ties_go_to_the_example_first_in_the_file() -> None:
    twins = [Example(id="a", lang="en", text="same words"), Example(id="b", lang="en", text="same words")]
    assert await Suggestions(twins, HashEmbedder()).closest("same words", "en", far()) == "a"


class Flaky:
    tag = "flaky"

    def __init__(self) -> None:
        self.fail = True
        self.inner = HashEmbedder()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self.fail:
            raise RuntimeError("embedder down")
        return await self.inner.embed(texts)


async def test_a_failed_warm_up_is_retried_on_first_use() -> None:
    embedder = Flaky()
    suggestions = Suggestions(EXAMPLES, embedder)
    with pytest.raises(RuntimeError):
        await suggestions.warm()
    embedder.fail = False
    assert await suggestions.closest("Which AWS services?", "en", far()) == "en-aws"
