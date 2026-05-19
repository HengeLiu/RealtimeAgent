from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

from audio_chat.asset import AssetRef
from audio_chat.agent_core.providers import DashScopeCompatibleTextModelAdapter, OpenAICompatibleTextModelAdapter
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
        assistant_message = next(item for item in messages if item["role"] == "assistant" and item.get("tool_calls"))
        provider_call = assistant_message["tool_calls"][0]
        assert provider_call["type"] == "function"
        assert provider_call["function"]["name"] == "city_lookup"
        assert json.loads(provider_call["function"]["arguments"]) == {"city": "shanghai"}
        tool_message = next(item for item in messages if item["role"] == "tool")
        assert json.loads(tool_message["content"])["data"]["city"] == "shanghai"
        yield "工具结果已回填。"

    def stream_text(self, transcript: str):
        """历史接口占位，当前测试不应调用。"""

        yield "unused"

    def cancel(self) -> None:
        """取消测试模型。"""


class TextThenToolCallingModel:
    """先输出一段自然语言，再发起工具调用的测试模型。"""

    provider_name = "mock-text-then-tool"
    model = "mock-text-then-tool-model"

    def __init__(self) -> None:
        self.calls = 0

    def stream_messages(self, *, messages: list[dict], tools: list[dict]):
        """模拟模型先说“我先查一下”，再返回 function call。"""

        self.calls += 1
        if self.calls == 1:
            yield "我先查一下。"
            yield {
                "type": "tool_call",
                "id": "call-city-1",
                "name": "city_lookup",
                "arguments": {"city": "shanghai"},
            }
            return
        yield "工具结果已回填。"

    def stream_text(self, transcript: str):
        """历史接口占位，当前测试不应调用。"""

        yield "unused"

    def cancel(self) -> None:
        """取消测试模型。"""


class CapturePhotoTool(BaseTool):
    """测试用抓拍 Tool。

    主要功能：返回一张本地 JPEG AssetRef，用于验证 Text 多模态 follow-up message。
    """

    name = "capture_photo"
    description = "采集当前画面"

    def __init__(self, image_path) -> None:
        self.image_path = image_path

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """返回固定图片资产。

        功能：模拟端侧抓拍完成后的 ToolResult。
        主要逻辑：不访问真实设备，只把测试图片文件包装成 AssetRef。
        参数：`context/input_data` 为工具上下文和模型参数。
        返回值：包含 assets 的 ToolResult。
        异常情况：测试不主动抛异常。
        """

        asset = AssetRef(
            asset_id="asset-photo-1",
            user_id=context.user_id,
            session_id=context.session_id,
            stream_type="sensor.rgb",
            mime_type="image/jpeg",
            created_at_ms=int(time.time() * 1000),
            uri=str(self.image_path),
            size_bytes=self.image_path.stat().st_size,
        )
        return ToolResult.success(
            data={"captured": True, "asset_id": asset.asset_id, "uri": asset.uri, "mime_type": asset.mime_type},
            assets=[asset],
            message="已获取当前画面。",
        )


class CapturePhotoVisionModel:
    """测试用多模态文本模型。

    主要功能：第一轮请求抓拍，第二轮校验 TextAgentCore 已把图片 block 拼入 messages。
    """

    provider_name = "mock-vision-tool"
    model = "mock-vision-tool-model"

    def __init__(self) -> None:
        self.calls: list[list[dict]] = []
        self.prompt = ""

    def stream_messages(self, *, messages: list[dict], tools: list[dict]):
        """模拟支持工具调用和图片 content block 的模型。"""

        self.calls.append(list(messages))
        if len(self.calls) == 1:
            yield {"type": "tool_call", "id": "call-photo-1", "name": "capture_photo", "arguments": {}}
            return
        followup = messages[-1]
        assert followup["role"] == "user"
        content = followup["content"]
        assert isinstance(content, list)
        image_block = next(item for item in content if item.get("type") == "image_url")
        assert image_block["image_url"]["url"].startswith("data:image/jpeg;base64,")
        yield "我看到当前画面了。"

    def stream_text(self, transcript: str):
        """历史接口占位。"""

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
    trace_text = (session_dir / "tool-events.jsonl").read_text(encoding="utf-8")
    assert "assistant_tool_call.done" in message_text
    assert "tool_result.done" in message_text
    assert "工具结果已回填。" in message_text
    assert "city_lookup" in trace_text


def test_text_agent_supports_text_delta_before_tool_call(tmp_path) -> None:
    """测试目标：验证 Text 链路兼容“先说话，后 function call”的模型输出顺序。

    测试方法：mock 模型第一轮先返回文本 delta，再返回 `city_lookup` tool_call；
    工具结果回填后第二轮返回最终文本。
    预期结果：先发出的文本会被保留，工具仍会真实执行，最终消息包含两段助手文本。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), agent_mode="text"))
    app.tool_registry.register(CityTool())
    app.agent_core.text_model = TextThenToolCallingModel()
    user_id = "user-text-then-tool"
    session_id = "sess-text-then-tool"

    app.agent_core.append_audio_event(
        StreamChunk(
            user_id=user_id,
            session_id=session_id,
            stream_id="stream-mic",
            stream_type="sensor.mic",
            seq=0,
            payload=b"hello",
            final=True,
        )
    )

    session_dir = tmp_path / "runs" / user_id / session_id
    message_text = (session_dir / "messages.jsonl").read_text(encoding="utf-8")
    trace_text = (session_dir / "tool-events.jsonl").read_text(encoding="utf-8")
    events_text = (session_dir / "agent-events.jsonl").read_text(encoding="utf-8")

    assert "我先查一下。工具结果已回填。" in message_text
    assert "assistant_tool_call.done" in message_text
    assert "tool_result.done" in message_text
    assert "city_lookup" in trace_text
    assert events_text.index("assistant_text.delta") < events_text.index("tool_call.delta")


def test_text_agent_attaches_capture_photo_asset_to_followup_message(tmp_path) -> None:
    """测试目标：验证 Text 链路在 capture_photo 后把图片资产拼入下一次模型请求。

    测试方法：注册返回本地 JPEG 的测试 Tool，mock 模型第一轮调用 capture_photo，
    第二轮断言 provider messages 最后一条包含 image_url content block。
    预期结果：模型收到图片 data URL，model-request.json 记录脱敏 image block 和
    visual_asset source map。
    """

    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg\xff\xd9")
    app = AudioChatApp(
        AudioChatConfig(
            runs_root=str(tmp_path / "runs"),
            agent_mode="text",
            text_multimodal_enabled=True,
            text_multimodal_attach_tool_result_assets=True,
            text_multimodal_max_image_base64_bytes=1024,
        )
    )
    app.tool_registry.register(CapturePhotoTool(image_path))
    model = CapturePhotoVisionModel()
    app.agent_core.text_model = model
    user_id = "user-vision"
    session_id = "sess-vision"

    app.agent_core.append_audio_event(
        StreamChunk(
            user_id=user_id,
            session_id=session_id,
            stream_id="stream-mic",
            stream_type="sensor.mic",
            seq=0,
            payload=b"hello",
            final=True,
        )
    )

    session_dir = tmp_path / "runs" / user_id / session_id
    request = json.loads((session_dir / "model-request.json").read_text(encoding="utf-8"))
    events_text = (session_dir / "agent-events.jsonl").read_text(encoding="utf-8")
    assert len(model.calls) == 2
    assert "multimodal.tool_asset.attached" in events_text
    assert any(item.get("source_id") == "visual_asset:asset-photo-1" for item in request["context_sources"])
    image_messages = [
        message
        for message in request["messages"]
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if isinstance(block, dict) and block.get("type") == "image_url"
    ]
    assert image_messages
    assert "<redacted:" in json.dumps(request["messages"], ensure_ascii=False)


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


def test_openai_compatible_stream_messages_allows_text_before_tool_call() -> None:
    """测试目标：验证 OpenAI-compatible provider 保留 tool call 前的文本 delta。

    测试方法：假 stream 先返回 content，再返回 tool_calls argument delta。
    预期结果：adapter 输出顺序为文本 delta 在前、统一 tool_call 在后。
    """

    adapter = OpenAICompatibleTextModelAdapter.__new__(OpenAICompatibleTextModelAdapter)
    adapter.model = "fake-model"
    adapter._cancelled = False
    adapter._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_fake_text_then_tool_stream)))

    items = list(adapter.stream_messages(messages=[{"role": "user", "content": "hi"}], tools=[{"type": "function"}]))

    assert items == [
        "我先查一下。",
        {
            "type": "tool_call",
            "id": "call-1",
            "name": "city_lookup",
            "arguments": {"city": "shanghai"},
        },
    ]


def test_dashscope_compatible_text_model_disables_thinking() -> None:
    """测试目标：验证 DashScope 兼容 Text provider 显式关闭 thinking。

    测试方法：绕过真实 OpenAI 客户端，注入假 chat.completions.create 并捕获请求参数。
    预期结果：请求通过 `extra_body` 携带 `enable_thinking=False`，避免 qwen3.6 默认思考模式增加延迟。
    """

    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="好", tool_calls=[]))])

    adapter = DashScopeCompatibleTextModelAdapter.__new__(DashScopeCompatibleTextModelAdapter)
    adapter.model = "qwen3.6-flash"
    adapter.prompt = "你是中文助手"
    adapter._cancelled = False
    adapter.endpoint = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    adapter.request_timeout_seconds = 15
    adapter.max_retries = 1
    adapter.extra_body = {"enable_thinking": False}
    adapter._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)))

    items = list(adapter.stream_messages(messages=[{"role": "user", "content": "看一下"}], tools=[]))

    assert items == ["好"]
    assert captured["extra_body"] == {"enable_thinking": False}
    assert adapter.request_options_snapshot()["extra_body"] == {"enable_thinking": False}


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


def _fake_text_then_tool_stream(**kwargs):
    """构造先 content 后 tool_call 的 OpenAI-compatible streaming chunk。"""

    _ = kwargs
    yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="我先查一下。", tool_calls=[]))])
    yield SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            index=0,
                            id="call-1",
                            function=SimpleNamespace(name="city_lookup", arguments='{"city": "shanghai"}'),
                        )
                    ],
                )
            )
        ]
    )
