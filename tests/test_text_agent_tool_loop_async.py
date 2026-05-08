from __future__ import annotations

import asyncio
from types import SimpleNamespace

from audio_chat.agent_core.providers import OpenAICompatibleTextModelAdapter
from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.protocol import StreamChunk
from audio_chat.tools import BaseTool, ToolContext, ToolResult


class CityTool(BaseTool):
    """测试用城市 Tool。

    主要功能：验证 TextAgentCore 在已有事件循环内也能安全调用 ToolGateway。
    主要方法：`run()` 返回工具入参和用户上下文。
    主要属性：`name` 是 provider function calling 使用的工具名。
    """

    name = "city_lookup"
    description = "查询城市信息"

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """执行测试工具。

        功能：返回城市名和当前用户 ID。
        主要逻辑：模拟真实异步 Tool，先让出一次事件循环。
        参数：`context` 为 SDK 注入的用户设备上下文；`input_data` 为模型参数。
        返回值：`ToolResult.success`。
        异常情况：本测试工具不主动抛异常。
        """

        await asyncio.sleep(0)
        return ToolResult.success({"city": input_data["city"], "user_id": context.user_id})


class ToolCallingModel:
    """会先发起工具调用、再根据 ToolResult 输出文本的测试模型。"""

    provider_name = "mock-tool"
    model = "mock-tool-model"

    def __init__(self) -> None:
        self.calls = 0

    def stream_messages(self, *, messages: list[dict], tools: list[dict]):
        """模拟 OpenAI-compatible 多轮工具循环。"""

        self.calls += 1
        if self.calls == 1:
            yield {
                "type": "tool_call",
                "id": "call-city-1",
                "name": "city_lookup",
                "arguments": {"city": "shanghai"},
            }
            return
        tool_message = next(item for item in messages if item["role"] == "tool")
        assert tool_message["content"]["data"]["city"] == "shanghai"
        yield "工具结果已回填。"

    def stream_text(self, transcript: str):
        """兼容旧接口，当前测试不应调用。"""

        yield "unused"

    def cancel(self) -> None:
        """取消测试模型。"""


def test_text_agent_tool_loop_is_safe_inside_running_event_loop(tmp_path) -> None:
    """测试目标：验证 TextAgentCore 在 aiohttp 事件循环内触发工具调用不会使用嵌套 `asyncio.run()`。

    测试方法：在 `asyncio.run()` 内直接调用同步 `append_audio_event()`，mock model
    先返回 tool_call，再检查 ToolResult 回填后的第二轮回复。
    预期结果：不会报 `asyncio.run() cannot be called from a running event loop`，并写入工具结果。
    """

    async def _run() -> None:
        app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), agent_mode="text"))
        app.tool_registry.register(CityTool())
        app.agent_core.text_model = ToolCallingModel()
        session_id = "sess-async-tool"

        app.agent_core.append_audio_event(
            StreamChunk(
                user_id="user-tool",
                session_id=session_id,
                stream_id="stream-mic",
                stream_type="sensor.mic",
                seq=0,
                payload=b"hello",
                final=True,
            )
        )

    asyncio.run(_run())

    session_dir = tmp_path / "runs" / "user-tool" / "sess-async-tool"
    message_text = (session_dir / "messages.jsonl").read_text(encoding="utf-8")
    trace_text = (session_dir / "tool-trace.jsonl").read_text(encoding="utf-8")
    assert "tool.result" in message_text
    assert "工具结果已回填。" in message_text
    assert "city_lookup" in trace_text


def test_openai_compatible_stream_messages_aggregates_tool_call_delta() -> None:
    """测试目标：验证 OpenAI-compatible provider 聚合 tool call argument delta。

    测试方法：用假 client 返回两个 arguments 片段和工具名片段。
    预期结果：输出一个 SDK 统一 `tool_call` 字典，arguments 被解析成 dict。
    """

    adapter = OpenAICompatibleTextModelAdapter.__new__(OpenAICompatibleTextModelAdapter)
    adapter.model = "fake-model"
    adapter._cancelled = False
    adapter._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_fake_chat_stream)))

    items = list(adapter.stream_messages(messages=[{"role": "user", "content": "hi"}], tools=[{"type": "function"}]))

    assert items == [
        {
            "type": "tool_call",
            "id": "call-1",
            "name": "city_lookup",
            "arguments": {"city": "shanghai"},
        }
    ]


def _fake_chat_stream(**kwargs):
    """构造 OpenAI-compatible streaming chunk。"""

    _ = kwargs
    yield SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            index=0,
                            id="call-1",
                            function=SimpleNamespace(name="city_lookup", arguments='{"city": '),
                        )
                    ],
                )
            )
        ]
    )
    yield SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            index=0,
                            id=None,
                            function=SimpleNamespace(name=None, arguments='"shanghai"}'),
                        )
                    ],
                )
            )
        ]
    )
