"""AC6: the OpenAI-compatible adapter reads a stream as providers send it."""

import httpx
import pytest
from rag_core.adapters.openai_compat import OpenAICompatProvider, _counts
from rag_core.ports import ChatChunk, ProviderError

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
        ({"prompt_tokens": 800}, None),  # half the counts would bill too little
        ({"prompt_tokens": float("inf"), "completion_tokens": 1}, None),
        ({"prompt_tokens": 10**15, "completion_tokens": 1}, None),
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


@pytest.mark.parametrize(
    ("message", "text"),
    [
        ({"content": "an answer"}, "an answer"),
        ({"content": None}, ""),  # a filter or a refusal: the model's miss
        ({"role": "assistant"}, ""),  # some providers leave the key out when nothing was written
        ({"content": [{"type": "text", "text": "x"}]}, None),  # parts lists aren't read: the provider's
        ({"content": {"text": "x"}}, None),
        ({"content": 7}, None),
    ],
)
async def test_reads_string_content_only(message: dict[str, object], text: str | None) -> None:
    body = {"choices": [{"message": message}]}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    provider = OpenAICompatProvider("https://provider.test/v1", "k", transport=transport)
    messages = [{"role": "user", "content": "q"}]
    if text is None:
        with pytest.raises(ProviderError, match="malformed body"):
            await provider.chat("m", messages, max_tokens=5, schema=None)
    else:
        assert (await provider.chat("m", messages, max_tokens=5, schema=None)).text == text
    await provider.close()


async def test_a_frame_we_cant_read_fails_the_stream_even_after_text() -> None:
    frames = (
        b'data: {"choices":[{"delta":{"content":"The answer is "}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":[{"type":"text","text":"42"}]}}]}\n\n'
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=frames))
    provider = OpenAICompatProvider("https://provider.test/v1", "k", transport=transport)
    with pytest.raises(ProviderError, match="malformed frame"):  # never an answer with a piece missing
        _ = [c async for c in provider.chat_stream("m", [{"role": "user", "content": "q"}], max_tokens=5)]
    await provider.close()


async def test_an_error_choice_is_the_providers_failure() -> None:
    body = {"choices": [{"message": {"role": "assistant"}, "finish_reason": "error", "error": {"code": 502}}]}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    provider = OpenAICompatProvider("https://provider.test/v1", "k", transport=transport)
    with pytest.raises(ProviderError):
        await provider.chat("m", [{"role": "user", "content": "q"}], max_tokens=5, schema=None)
    await provider.close()


async def test_a_streamed_error_choice_after_text_is_the_providers_failure() -> None:
    frames = (
        b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
        b'data: {"choices":[{"delta":{},"finish_reason":"error"}]}\n\n'
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=frames))
    provider = OpenAICompatProvider("https://provider.test/v1", "k", transport=transport)
    with pytest.raises(ProviderError):
        _ = [c async for c in provider.chat_stream("m", [{"role": "user", "content": "q"}], max_tokens=5)]
    await provider.close()
