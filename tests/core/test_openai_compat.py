"""AC6: the OpenAI-compatible adapter reads a stream as providers send it."""

import json

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
        ({"content": None}, ""),
        ({"role": "assistant"}, ""),  # some providers leave the key out when nothing was written
        (
            {
                "content": [
                    {"type": "text", "text": "an"},
                    {"type": "reasoning", "text": "hmm"},
                    None,
                    {"text": "swer"},
                ]
            },
            "answer",
        ),  # reasoning never reaches the answer
    ],
)
async def test_reads_null_and_part_list_content(message: dict[str, object], text: str) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"choices": [{"message": message}]})
    )
    provider = OpenAICompatProvider("https://provider.test/v1", "k", transport=transport)
    result = await provider.chat("m", [{"role": "user", "content": "q"}], max_tokens=5, schema=None)
    await provider.close()
    assert result.text == text


@pytest.mark.parametrize("message", [{"content": 7}, {"content": True}])
async def test_a_malformed_message_is_the_providers_failure(message: dict[str, object]) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"choices": [{"message": message}]})
    )
    provider = OpenAICompatProvider("https://provider.test/v1", "k", transport=transport)
    with pytest.raises(ProviderError):
        await provider.chat("m", [{"role": "user", "content": "q"}], max_tokens=5, schema=None)
    await provider.close()


async def test_a_streamed_part_list_reads_as_text() -> None:
    frames = b'data: {"choices":[{"delta":{"content":[{"type":"text","text":"hi"}]}}]}\n\ndata: [DONE]\n\n'
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=frames))
    provider = OpenAICompatProvider("https://provider.test/v1", "k", transport=transport)
    chunks = [c async for c in provider.chat_stream("m", [{"role": "user", "content": "q"}], max_tokens=5)]
    await provider.close()
    assert chunks == [ChatChunk(text="hi")]


async def test_an_error_choice_is_the_providers_failure() -> None:
    body = {"choices": [{"message": {"role": "assistant"}, "finish_reason": "error", "error": {"code": 502}}]}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    provider = OpenAICompatProvider("https://provider.test/v1", "k", transport=transport)
    with pytest.raises(ProviderError):
        await provider.chat("m", [{"role": "user", "content": "q"}], max_tokens=5, schema=None)
    await provider.close()


async def test_an_odd_streamed_delta_is_no_text_not_a_failure() -> None:
    frames = (
        b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":7}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":" there"}}]}\n\ndata: [DONE]\n\n'
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=frames))
    provider = OpenAICompatProvider("https://provider.test/v1", "k", transport=transport)
    chunks = [c async for c in provider.chat_stream("m", [{"role": "user", "content": "q"}], max_tokens=5)]
    await provider.close()
    assert "".join(c.text for c in chunks) == "Hello there"


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


async def test_a_stream_with_no_readable_text_is_the_providers_failure() -> None:
    frames = b'data: {"choices":[{"delta":{"content":7}}]}\n\ndata: [DONE]\n\n'
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=frames))
    provider = OpenAICompatProvider("https://provider.test/v1", "k", transport=transport)
    with pytest.raises(ProviderError):
        _ = [c async for c in provider.chat_stream("m", [{"role": "user", "content": "q"}], max_tokens=5)]
    await provider.close()


async def test_an_odd_part_type_is_skipped_not_a_crash() -> None:
    message = {"content": [{"type": ["text"], "text": "x"}, {"text": "ok"}]}
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"choices": [{"message": message}]})
    )
    provider = OpenAICompatProvider("https://provider.test/v1", "k", transport=transport)
    result = await provider.chat("m", [{"role": "user", "content": "q"}], max_tokens=5, schema=None)
    await provider.close()
    assert result.text == "ok"


@pytest.mark.parametrize(
    ("content", "unreadable"),
    [
        ([7], True),
        ([{"type": ["text"], "text": "x"}], True),
        ({}, True),
        ([{"type": "reasoning", "text": "x"}], False),
    ],
)
async def test_parts_with_nothing_recognizable_are_unreadable(content: object, unreadable: bool) -> None:
    frame = json.dumps({"choices": [{"delta": {"content": content}}]}).encode()
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"data: " + frame + b"\n\ndata: [DONE]\n\n")
    )
    provider = OpenAICompatProvider("https://provider.test/v1", "k", transport=transport)
    try:
        _ = [c async for c in provider.chat_stream("m", [{"role": "user", "content": "q"}], max_tokens=5)]
    except ProviderError:
        failed = True
    else:
        failed = False
    await provider.close()
    assert failed is unreadable
