"""AC6: the OpenAI-compatible adapter reads a stream as providers send it."""

import httpx
import pytest
from rag_core.adapters.openai_compat import OpenAICompatProvider, _counts
from rag_core.ports import ChatChunk

FRAMES = (
    b": OPENROUTER PROCESSING\n\n"
    b'data: {"choices":[{"index":0,"delta":{"role":"assistant"}}]}\n\n'
    b'data: {"choices":[{"index":0,"delta":{"content":"Hi"}}]}\n\n'
    b'data: {"choices":[{"index":0,"delta":{"content":" there"}}],'
    b'"usage":{"prompt_tokens":7,"completion_tokens":2}}\n\n'
    b"data: [DONE]\n\n"
)


async def test_reads_text_usage_and_textless_frames_and_skips_keep_alives() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=FRAMES))
    provider = OpenAICompatProvider("https://provider.test/v1", "k", transport=transport)
    chunks = [c async for c in provider.chat_stream("m", [{"role": "user", "content": "q"}], max_tokens=5)]
    await provider.close()
    assert chunks == [
        ChatChunk(),  # the role frame: no text, still the model's
        ChatChunk(text="Hi"),
        ChatChunk(text=" there", tokens_in=7, tokens_out=2),  # text and usage in one frame
    ]


async def test_a_usage_frame_with_an_odd_choice_still_reads() -> None:
    frames = b'data: {"choices":[null],"usage":{"prompt_tokens":3,"completion_tokens":1}}\n\ndata: [DONE]\n\n'
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=frames))
    provider = OpenAICompatProvider("https://provider.test/v1", "k", transport=transport)
    chunks = [c async for c in provider.chat_stream("m", [{"role": "user", "content": "q"}], max_tokens=5)]
    await provider.close()
    assert chunks == [ChatChunk(tokens_in=3, tokens_out=1)]


async def test_null_token_counts_are_no_counts() -> None:
    frames = (
        b'data: {"choices":[{"delta":{"content":"end"}}],'
        b'"usage":{"prompt_tokens":null,"completion_tokens":4}}\n\ndata: [DONE]\n\n'
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=frames))
    provider = OpenAICompatProvider("https://provider.test/v1", "k", transport=transport)
    chunks = [c async for c in provider.chat_stream("m", [{"role": "user", "content": "q"}], max_tokens=5)]
    await provider.close()
    assert chunks == [ChatChunk(text="end")]  # the counts can't be trusted half-way: none are taken


async def test_a_completion_with_null_content_or_counts_still_reads() -> None:
    body = {
        "choices": [{"message": {"content": None}}],
        "usage": {"prompt_tokens": None, "completion_tokens": 4},
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    provider = OpenAICompatProvider("https://provider.test/v1", "k", transport=transport)
    result = await provider.chat("m", [{"role": "user", "content": "q"}], max_tokens=5, schema=None)
    await provider.close()
    assert (result.text, result.tokens_in, result.tokens_out) == ("", 0, 0)  # no counts to trust


@pytest.mark.parametrize(
    ("usage", "counts"),
    [
        ({"prompt_tokens": 800}, (800, 0)),
        ({"prompt_tokens": 7.0, "completion_tokens": 2.0}, (7, 2)),
        ({"prompt_tokens": -500, "completion_tokens": 10}, None),
        ({"prompt_tokens": True, "completion_tokens": 1}, None),
        ({"prompt_tokens": "7", "completion_tokens": 1}, None),
        ({}, None),
    ],
)
def test_token_counts_are_whole_and_non_negative(
    usage: dict[str, object], counts: tuple[int, int] | None
) -> None:
    assert _counts(usage) == counts
