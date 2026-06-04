from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

from realtime_agent.asset import AssetRef
from realtime_agent.conversation.providers import DashScopeCompatibleVisionModelAdapter, OpenAICompatibleVisionModelAdapter
from realtime_agent.app import RealtimeAgentApp, RealtimeAgentConfig
from realtime_agent.protocol import Event, StreamChunk, StreamFormat
from realtime_agent.tools import BaseTool, ToolContext, ToolResult, VisualAssetRef


class CityTool(BaseTool):
    """测试用城市 Tool。

    主要功能：验证 VisionRealtimeAgentCore 在已有事件循环内也能安全调用 ToolGateway。
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

    provider_name = "mock-vision-then-tool"
    model = "mock-vision-then-tool-model"

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

    主要功能：返回一张本地 JPEG AssetRef，用于验证 Vision 多模态 follow-up message。
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
            visual_assets=[
                VisualAssetRef(
                    asset=asset,
                    visibility="append_to_agent",
                    consumer="agent_inline",
                    text_context="这是测试抓拍的当前画面。",
                    claim_required=False,
                )
            ],
            message="已获取当前画面。",
        )


class CapturePhotoVisionModel:
    """测试用多模态Vision 模型。

    主要功能：第一轮请求抓拍，第二轮校验 VisionRealtimeAgentCore 已把图片 block 拼入 messages。
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


class VisibilityPhotoTool(BaseTool):
    """测试视觉资产 visibility 的抓拍 Tool。"""

    name = "capture_photo"
    description = "采集当前画面"

    def __init__(self, image_path, *, visibility: str | None) -> None:
        self.image_path = image_path
        self.visibility = visibility

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """按指定 visibility 返回图片资产。"""

        asset = AssetRef(
            asset_id=f"asset-{self.visibility or 'assets-only'}",
            user_id=context.user_id,
            session_id=context.session_id,
            stream_type="sensor.rgb",
            mime_type="image/jpeg",
            created_at_ms=int(time.time() * 1000),
            uri=str(self.image_path),
            size_bytes=self.image_path.stat().st_size,
        )
        visual_assets = []
        if self.visibility is not None:
            visual_assets.append(
                VisualAssetRef(
                    asset=asset,
                    visibility=self.visibility,  # type: ignore[arg-type]
                    consumer="tool_internal" if self.visibility == "internal_only" else "agent_inline",
                    text_context="visibility 测试图片。",
                    claim_required=False,
                )
            )
        return ToolResult.success(
            data={"captured": True, "asset_id": asset.asset_id},
            assets=[asset],
            visual_assets=visual_assets,
            message="已获取当前画面。",
        )


class NoImplicitVisualAppendModel:
    """确认非 append_to_agent 资产不会被拼入模型的测试模型。"""

    provider_name = "mock-no-implicit-visual"
    model = "mock-no-implicit-visual-model"

    def __init__(self) -> None:
        self.calls: list[list[dict]] = []
        self.prompt = ""

    def stream_messages(self, *, messages: list[dict], tools: list[dict]):
        """第一轮请求抓拍，第二轮确认没有 image_url。"""

        self.calls.append(list(messages))
        if len(self.calls) == 1:
            yield {"type": "tool_call", "id": "call-photo-1", "name": "capture_photo", "arguments": {}}
            return
        assert not _messages_contain_image_block(messages)
        yield "我只能看到工具文本结果。"

    def stream_text(self, transcript: str):
        """历史接口占位。"""

        yield "unused"

    def cancel(self) -> None:
        """取消测试模型。"""


class TurnBufferVisionModel:
    """测试用 turn buffer 多模态模型。"""

    provider_name = "mock-turn-buffer"
    model = "mock-turn-buffer-model"

    def __init__(self) -> None:
        self.calls: list[list[dict]] = []
        self.prompt = ""

    def stream_messages(self, *, messages: list[dict], tools: list[dict]):
        """校验当前 turn buffer 图片会在首轮模型请求前批量 append。"""

        self.calls.append(list(messages))
        followup = messages[-1]
        assert followup["role"] == "user"
        content = followup["content"]
        assert isinstance(content, list)
        text_block = next(item for item in content if item.get("type") == "text")
        assert "第 1 张" in text_block["text"]
        assert "direction=front" in text_block["text"]
        image_block = next(item for item in content if item.get("type") == "image_url")
        assert image_block["image_url"]["url"].startswith("data:image/jpeg;base64,")
        yield "已结合实时画面。"

    def stream_text(self, transcript: str):
        """历史接口占位。"""

        yield "unused"

    def cancel(self) -> None:
        """取消测试模型。"""


class RgbCaptureConnection:
    """测试用 RGB 端侧连接。

    主要功能：收到服务端 `stream.control.open.requested` 后立刻上传一帧 JPEG。
    主要属性：`events` 保存控制事件，`opened_requests` 记录服务端主动采集请求。
    """

    def __init__(self, *, app: RealtimeAgentApp, device_id: str, image: bytes) -> None:
        self.app = app
        self.device_id = device_id
        self.image = image
        self.events: list[Event] = []
        self.opened_requests: list[Event] = []

    def push_event(self, event: Event) -> None:
        """响应服务端下发的 RGB 采集控制事件。"""

        self.events.append(event)
        if event.event_name != "stream.control.open.requested" or event.stream_type != "sensor.rgb":
            return
        self.opened_requests.append(event)
        handle = self.app.open_input_stream(
            user_id=event.user_id,
            producer_id=self.device_id,
            stream_type="sensor.rgb",
            format=StreamFormat(codec="jpeg", sample_rate=1, channels=1, chunk_ms=0),
        )
        self.app.write_input_chunk(
            StreamChunk(
                user_id=event.user_id,
                session_id=self.device_id,
                stream_id=handle.stream_id,
                stream_type="sensor.rgb",
                seq=len(self.opened_requests) - 1,
                payload=self.image,
                codec="jpeg",
                sample_rate=1,
                channels=1,
                final=True,
                metadata={
                    "request_id": event.payload.get("request_id"),
                    "turn_id": event.session_id or self.device_id,
                    "ttl_seconds": event.payload.get("ttl_seconds"),
                    "capture_reason": event.payload.get("capture_reason"),
                    "captured_at_ms": int(time.time() * 1000),
                    "sequence_index": len(self.opened_requests) - 1,
                    "direction": event.payload.get("direction") or "front",
                },
            )
        )

    def push_stream_chunk(self, chunk: StreamChunk) -> None:
        """本测试不消费下行音频分片。"""

        _ = chunk


def register_rgb_device(app: RealtimeAgentApp, connection: RgbCaptureConnection, user_id: str) -> None:
    """注册支持 RGB 单帧采集的测试端侧。"""

    app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id=user_id,
            producer_id=connection.device_id,
            payload={
                "device_id": connection.device_id,
                "auth": {"mode": "disabled"},
                "supports": {"sensors": [{"type": "rgb", "modes": ["single", "continuous"]}], "actuators": []},
            },
        ),
        connection,
    )


def test_vision_agent_tool_loop_is_safe_inside_running_event_loop(tmp_path) -> None:
    """测试目标：验证 VisionRealtimeAgentCore 在 aiohttp 事件循环内触发工具调用不会使用嵌套 `asyncio.run()`。

    测试方法：在 `asyncio.run()` 内直接调用同步 `append_audio_event()`，mock model
    先返回 tool_call，再检查 ToolResult 回填后的第二轮回复。
    预期结果：不会报 `asyncio.run() cannot be called from a running event loop`，并写入工具结果。
    """

    async def _run() -> None:
        app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
        app.tool_registry.register(CityTool())
        app.agent_core.vision_model = ToolCallingModel()
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


def test_vision_agent_supports_vision_delta_before_tool_call(tmp_path) -> None:
    """测试目标：验证 Vision 链路兼容“先说话，后 function call”的模型输出顺序。

    测试方法：mock 模型第一轮先返回文本 delta，再返回 `city_lookup` tool_call；
    工具结果回填后第二轮返回最终文本。
    预期结果：先发出的文本会被保留，工具仍会真实执行，最终消息包含两段助手文本。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    app.tool_registry.register(CityTool())
    app.agent_core.vision_model = TextThenToolCallingModel()
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


def test_vision_agent_attaches_capture_photo_asset_to_followup_message(tmp_path) -> None:
    """测试目标：验证 Vision 链路在 capture_photo 后把图片资产拼入下一次模型请求。

    测试方法：注册返回本地 JPEG 的测试 Tool，mock 模型第一轮调用 capture_photo，
    第二轮断言 provider messages 最后一条包含 image_url content block。
    预期结果：模型收到图片 data URL，model-request.json 记录脱敏 image block 和
    visual_asset source map。
    """

    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg\xff\xd9")
    app = RealtimeAgentApp(
        RealtimeAgentConfig(
            runs_root=str(tmp_path / "runs"),
            agent_mode="vision",
            vision_multimodal_enabled=True,
            vision_multimodal_attach_visual_assets=True,
            vision_multimodal_max_image_base64_bytes=1024,
        )
    )
    app.tool_registry.register(CapturePhotoTool(image_path))
    model = CapturePhotoVisionModel()
    app.agent_core.vision_model = model
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


def test_vision_agent_does_not_append_assets_without_append_visibility(tmp_path) -> None:
    """测试目标：验证 `ToolResult.assets` 和 `internal_only` 不会让主模型看到原图。

    测试方法：分别运行只返回 `assets`、返回 `internal_only visual_assets` 的抓拍 Tool。
    预期结果：第二轮模型 messages 中没有 image_url content block。
    """

    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg\xff\xd9")
    for visibility in (None, "internal_only"):
        app = RealtimeAgentApp(
            RealtimeAgentConfig(
                runs_root=str(tmp_path / f"runs-{visibility or 'assets-only'}"),
                agent_mode="vision",
                vision_multimodal_enabled=True,
                vision_multimodal_attach_visual_assets=True,
                vision_multimodal_max_image_base64_bytes=1024,
            )
        )
        app.tool_registry.register(VisibilityPhotoTool(image_path, visibility=visibility))
        model = NoImplicitVisualAppendModel()
        app.agent_core.vision_model = model
        app.agent_core.append_audio_event(
            StreamChunk(
                user_id=f"user-{visibility or 'assets-only'}",
                session_id=f"sess-{visibility or 'assets-only'}",
                stream_id="stream-mic",
                stream_type="sensor.mic",
                seq=0,
                payload=b"hello",
                final=True,
            )
        )
        assert len(model.calls) == 2


def test_vision_agent_batches_turn_buffer_assets_before_model_request(tmp_path) -> None:
    """测试目标：验证 Vision/VL 会在模型请求前批量 append 当前 turn buffer 图片。

    测试方法：先通过 AssetService 上传一帧 `sensor.rgb`，再触发一次 Vision 文本 turn。
    预期结果：首轮 provider messages 已包含带顺序、时间和 direction 的图片说明，
    图片资产被 claim，model-request 中只记录脱敏 image block。
    """

    app = RealtimeAgentApp(
        RealtimeAgentConfig(
            runs_root=str(tmp_path / "runs"),
            agent_mode="vision",
            vision_multimodal_enabled=True,
            vision_multimodal_attach_visual_assets=True,
            vision_multimodal_max_image_base64_bytes=1024,
        )
    )
    model = TurnBufferVisionModel()
    app.agent_core.vision_model = model
    user_id = "user-turn-buffer"
    session_id = "sess-turn-buffer"
    app.asset_service.store_chunk(
        StreamChunk(
            user_id=user_id,
            session_id=session_id,
            stream_id="rgb-stream-turn",
            stream_type="sensor.rgb",
            seq=0,
            payload=b"\xff\xd8turn-buffer-jpeg\xff\xd9",
            final=True,
            metadata={
                "turn_id": session_id,
                "ttl_seconds": 5,
                "capture_reason": "realtime_video",
                "captured_at_ms": 1760000001000,
                "sequence_index": 0,
                "direction": "front",
            },
        )
    )

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
    assets_text = (session_dir / "assets.jsonl").read_text(encoding="utf-8")

    assert len(model.calls) == 1
    assert "multimodal.turn_asset.attached" in events_text
    assert "asset.claimed" in assets_text
    assert any(item.get("reason") == "realtime_video_turn_flush" for item in request["context_sources"])
    assert "<redacted:" in json.dumps(request["messages"], ensure_ascii=False)


def test_vision_realtime_video_auto_collects_frame_without_tool(tmp_path) -> None:
    """测试目标：验证 Vision 链路在用户语音 turn 内会自动采集 RGB 帧。

    测试方法：注册支持 RGB 的测试端侧，让服务端在处理最终音频前主动下发
    `stream.control.open.requested`，端侧同步上传 JPEG。
    预期结果：不依赖 Tool 调用，首轮 VL 模型请求已经包含 realtime-video 图片，
    并记录采集、buffer 和 claim 事件。
    """

    app = RealtimeAgentApp(
        RealtimeAgentConfig(
            runs_root=str(tmp_path / "runs"),
            agent_mode="vision",
            vision_multimodal_enabled=True,
            vision_multimodal_attach_visual_assets=True,
            vision_multimodal_max_image_base64_bytes=1024,
            visual_realtime_video_enabled=True,
            visual_realtime_video_frame_interval_seconds=0.05,
            visual_realtime_video_frame_timeout_seconds=0.5,
            visual_realtime_video_frame_ttl_seconds=5,
            visual_realtime_video_max_frames_per_turn=1,
            visual_realtime_video_direction="front",
        )
    )
    user_id = "user-vision-auto-video"
    session_id = "sess-vision-auto-video"
    connection = RgbCaptureConnection(app=app, device_id=session_id, image=b"\xff\xd8auto-rgb\xff\xd9")
    register_rgb_device(app, connection, user_id)
    model = TurnBufferVisionModel()
    app.agent_core.vision_model = model

    app.agent_core.append_audio_event(
        StreamChunk(
            user_id=user_id,
            session_id=session_id,
            stream_id="stream-mic-auto-video",
            stream_type="sensor.mic",
            seq=0,
            payload=b"hello",
            final=True,
        )
    )

    session_dir = tmp_path / "runs" / user_id / session_id
    request = json.loads((session_dir / "model-request.json").read_text(encoding="utf-8"))
    events_text = (session_dir / "agent-events.jsonl").read_text(encoding="utf-8")
    assets_text = (session_dir / "assets.jsonl").read_text(encoding="utf-8")

    assert connection.opened_requests
    assert len(model.calls) == 1
    assert "vision.visual_frame.buffered" in events_text
    assert "multimodal.turn_asset.attached" in events_text
    assert "asset.claimed" in assets_text
    assert any(item.get("reason") == "realtime_video_turn_flush" for item in request["context_sources"])


def _messages_contain_image_block(messages: list[dict]) -> bool:
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        if any(isinstance(block, dict) and block.get("type") == "image_url" for block in content):
            return True
    return False


def test_openai_compatible_stream_messages_aggregates_tool_call_delta() -> None:
    """测试目标：验证 OpenAI-compatible Vision provider 聚合 tool call argument delta。

    测试方法：用假 client 返回两个 arguments 片段和工具名片段。
    预期结果：输出一个 SDK 统一 `tool_call` 字典，arguments 被解析成 dict。
    """

    adapter = OpenAICompatibleVisionModelAdapter.__new__(OpenAICompatibleVisionModelAdapter)
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
    """测试目标：验证 OpenAI-compatible Vision provider 保留 tool call 前的文本 delta。

    测试方法：假 stream 先返回 content，再返回 tool_calls argument delta。
    预期结果：adapter 输出顺序为文本 delta 在前、统一 tool_call 在后。
    """

    adapter = OpenAICompatibleVisionModelAdapter.__new__(OpenAICompatibleVisionModelAdapter)
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
    """测试目标：验证 DashScope 兼容 Vision provider 显式关闭 thinking。

    测试方法：绕过真实 OpenAI 客户端，注入假 chat.completions.create 并捕获请求参数。
    预期结果：请求通过 `extra_body` 携带 `enable_thinking=False`，避免 qwen3.6 默认思考模式增加延迟。
    """

    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="好", tool_calls=[]))])

    adapter = DashScopeCompatibleVisionModelAdapter.__new__(DashScopeCompatibleVisionModelAdapter)
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
