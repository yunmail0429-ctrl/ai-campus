import asyncio
import json

import httpx
import pytest

from app.adapters import ChatAdapter, ChatSpec, Connection


@pytest.mark.parametrize("mode", ["authorization", "api-key"])
def test_provider_authentication_header_and_stream_usage(mode):
    def handle(request):
        if mode == "api-key":
            assert request.headers["api-key"] == "test-secret"
            assert "authorization" not in request.headers
        else:
            assert request.headers["authorization"] == "Bearer test-secret"
            assert "api-key" not in request.headers
        frames = [
            {"choices": [{"index": 0, "delta": {"content": "连接成功"}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 4}},
        ]
        body = "".join("data: " + json.dumps(frame) + "\n\n" for frame in frames) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)

    async def read():
        adapter = ChatAdapter(
            Connection("https://example.test/v1", "test-secret", auth_header=mode),
            transport=httpx.MockTransport(handle),
        )
        return [
            event
            async for event in adapter.stream(ChatSpec("test", 64, 10), [{"role": "user", "content": "hi"}])
        ]

    events = asyncio.run(read())
    assert events[-1] == {"event": "usage", "inputTokens": 10, "outputTokens": 4}
    assert events[0]["delta"] == "连接成功"
