"""AC6: the OpenAI-compatible adapter reads a stream as providers send it."""

import httpx
from rag_core.adapters.openai_compat import OpenAICompatProvider
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
