"""百炼对话服务测试。"""

from __future__ import annotations

from nextgen.apps.server.conversation.dashscope_chat_service import DashscopeChatService


class _FakeStreamResponse:
    """模拟 httpx 流式响应。"""

    def __init__(self, lines):
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self) -> None:
        return None

    def iter_lines(self):
        for item in self.lines:
            yield item


class _FakeHttpClient:
    """模拟 httpx.Client。"""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def stream(self, method, url, headers=None, json=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "json": json})
        return _FakeStreamResponse(
            [
                'data: {"choices":[{"delta":{"content":"你好"}}]}',
                'data: {"choices":[{"delta":{"content":"，我是眼镜助手。"}}]}',
                "data: [DONE]",
            ]
        )


def test_dashscope_chat_service_can_parse_stream(monkeypatch) -> None:
    """验证百炼兼容接口的 SSE 流可以被正确解析。"""

    monkeypatch.setattr("nextgen.apps.server.conversation.dashscope_chat_service.httpx.Client", _FakeHttpClient)
    service = DashscopeChatService(api_key="test-key")

    chunks = list(service.stream_reply([{"role": "user", "content": "自我介绍一下"}]))

    assert chunks == ["你好", "，我是眼镜助手。"]
