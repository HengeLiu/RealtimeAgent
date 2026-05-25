from __future__ import annotations

import json
import sys
import time
import types
from pathlib import Path

from realtime_agent.agent_core.omni import (
    OMNI_REALTIME_IMAGE_MAX_BYTES,
    REALTIME_TOOL_CALL_PROMPT_RULE,
    MockRealtimeProviderAdapter,
    OmniRealtimeAgentCore,
    RealtimeProviderCallbacks,
    RealtimeProviderConfig,
    RealtimeProviderConcurrencyLimitError,
    RealtimeToolBridge,
    _prepare_omni_realtime_image,
)
from realtime_agent.app import RealtimeAgentApp, RealtimeAgentConfig
from realtime_agent.asset.service import AssetRef
from realtime_agent.protocol import Event, StreamChunk, StreamFormat
from realtime_agent.tools import BaseTool, ToolContext, ToolResult


class Connection:
    """测试用端侧连接。

    主要功能：收集 Control Service 推送的事件和 Stream Service 下发的音频 chunk。
    主要属性：`events` 保存控制事件，`chunks` 保存下行 stream chunk。
    """

    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.events = []
        self.chunks = []

    def push_event(self, event: Event) -> None:
        """记录服务端下发事件。"""
        self.events.append(event)

    def push_stream_chunk(self, chunk: StreamChunk) -> None:
        """记录服务端下发音频 chunk。"""
        self.chunks.append(chunk)


def register_speaker(app: RealtimeAgentApp, connection: Connection, user_id: str = "user-001") -> None:
    """注册一个可消费 actuator.speaker 的测试端侧。"""
    app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id=user_id,
            producer_id=connection.device_id,
            payload={
                "device_id": connection.device_id,
                "auth": {"mode": "disabled"},
                "supports": {"sensors": [], "actuators": []},
                "properties": {"realtime_agent.audio_output": "actuator.speaker"},
            },
        ),
        connection,
    )


def register_speaker_and_rgb(app: RealtimeAgentApp, connection: Connection, user_id: str = "user-001") -> None:
    """注册一个同时支持扬声器和 RGB 单帧采集的测试端侧。"""

    app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id=user_id,
            producer_id=connection.device_id,
            payload={
                "device_id": connection.device_id,
                "auth": {"mode": "disabled"},
                "supports": {
                    "sensors": [{"type": "rgb", "modes": ["single", "continuous"]}],
                    "actuators": [],
                },
                "properties": {"realtime_agent.audio_output": "actuator.speaker"},
            },
        ),
        connection,
    )


class FakeRealtimeProvider:
    """测试用 fake realtime provider。

    主要功能：记录 append/cancel/close 调用，并主动模拟 Omni audio delta/done 事件。
    主要属性：`callbacks` 保存 core 注入的回调，`appended` 保存收到的 mic chunk。
    """

    def __init__(self, config: RealtimeProviderConfig) -> None:
        self.config = config
        self.callbacks: RealtimeProviderCallbacks | None = None
        self.appended: list[StreamChunk] = []
        self.images: list[tuple[bytes, dict]] = []
        self.cancelled = False
        self.closed = False

    def open(self, *, user_id: str, session_id: str, callbacks: RealtimeProviderCallbacks) -> None:
        """打开 fake 会话。

        主要逻辑：只保存 callbacks，不访问网络。
        参数：`user_id`、`session_id` 用于匹配真实 provider 接口。
        返回值：无。
        异常情况：无。
        """
        self.callbacks = callbacks
        callbacks.provider_event({"event": "omni.session.opened", "provider": "fake"})

    def append_audio(self, chunk: StreamChunk) -> None:
        """记录输入音频并模拟 provider 下发 audio delta。

        主要逻辑：不要求 `chunk.final`，每次 append 都回调一片 24k PCM。
        参数：`chunk` 为 sensor.mic StreamChunk。
        返回值：无。
        异常情况：callbacks 未初始化时断言失败。
        """
        assert self.callbacks is not None
        self.appended.append(chunk)
        self.callbacks.audio_delta(
            b"\x01\x00" * 480,
            StreamFormat(codec="pcm16le", sample_rate=24000, channels=1, chunk_ms=20),
            {"provider": "fake", "model": self.config.model},
        )

    def emit_done(self) -> None:
        """模拟 provider audio done。

        主要逻辑：调用 core 注入的 audio_done callback。
        参数：无。
        返回值：无。
        异常情况：callbacks 未初始化时断言失败。
        """
        assert self.callbacks is not None
        self.callbacks.audio_done({"provider": "fake", "model": self.config.model})

    def commit_input(self, *, user_id: str, session_id: str, reason: str) -> None:
        """记录输入提交并模拟 provider 输出完成。"""
        self.emit_done()

    def append_image(self, image: bytes, *, user_id: str, session_id: str, metadata: dict | None = None) -> None:
        """记录追加到 fake provider 的图片。"""
        self.images.append((image, dict(metadata or {})))

    def cancel(self, *, user_id: str, reason: str) -> None:
        """记录 cancel 调用。"""
        self.cancelled = True

    def close(self, *, user_id: str, reason: str) -> None:
        """记录 close 调用。"""
        self.closed = True


def test_realtime_provider_config_defaults_to_ten_concurrent_sessions() -> None:
    """测试目标：确认 Realtime provider 默认并发上限为 10。

    测试方法：直接创建默认 `RealtimeProviderConfig`。
    预期结果：未显式配置时，SDK 使用 10 作为真实 provider 并发连接上限。
    """

    assert RealtimeProviderConfig().max_concurrent_sessions == 10


class FixedSummaryAgent:
    """测试用会话摘要器。"""

    def summarize(self, *, previous_summary: str, messages: list[dict]) -> str:
        """返回固定结构化摘要。"""

        return "当前对话状态：\n- 实时压缩前消息 0 已被归档。"


class FailingRealtimeProvider(FakeRealtimeProvider):
    """测试用失败 provider。

    主要功能：模拟 provider 在首次 append 时连接已关闭。
    主要属性：`append_calls` 用于确认 core 不会对失败 session 持续 append。
    """

    def __init__(self, config: RealtimeProviderConfig) -> None:
        super().__init__(config)
        self.append_calls = 0

    def append_audio(self, chunk: StreamChunk) -> None:
        """模拟 provider append 失败。"""
        self.append_calls += 1
        raise RuntimeError("Connection is already closed.")


class EchoRealtimeTool(BaseTool):
    """测试用 Realtime 工具。"""

    name = "echo_realtime"
    description = "Echo a value for realtime tool schema tests."
    input_model = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """返回输入值。"""

        return ToolResult.success({"value": input_data.get("value")})


class CapturePhotoRealtimeTool(BaseTool):
    """测试用 Realtime 视觉工具。

    主要功能：模拟可暴露给 Omni Realtime 的抓拍工具。
    主要方法：`run()` 返回一次成功抓拍结果。
    主要属性：`name` 用于验证工具 schema 注入。
    """

    name = "capture_photo"
    description = "Capture a photo for realtime vision."
    input_model = {
        "type": "object",
        "properties": {},
    }

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """返回模拟抓拍结果。"""

        return ToolResult.success({"captured": True})


class InterpretCurrentViewRealtimeExcludedTool(BaseTool):
    """测试用Vision 链路视觉工具。

    主要功能：模拟 `拍照 + 独立图片解读` 的Vision 链路复合工具。
    主要方法：`run()` 返回图片解读文本。
    主要属性：`name` 用于验证该工具不会暴露给 Omni Realtime。
    """

    name = "interpret_current_view"
    description = "Interpret current view for text agent."
    input_model = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
    }

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """返回模拟图片解读结果。"""

        return ToolResult.success({"interpretation": "测试结果"})


class ArgumentCaptureGateway:
    """测试用工具网关。

    主要功能：记录 RealtimeToolBridge 最终传给工具的参数。
    主要属性：`input_data` 保存最近一次工具调用入参。
    """

    def __init__(self) -> None:
        self.input_data: dict | None = None

    def provider_schemas(self) -> list[dict]:
        """返回空工具 schema，当前测试不验证 schema。"""

        return []

    def call_sync_safe(self, *, name: str, user_id: str, session_id: str, input_data: dict) -> ToolResult:
        """记录入参并返回成功结果。"""

        self.input_data = input_data
        return ToolResult.success({"received": input_data})


def _realtime_app(tmp_path, instances: list[FakeRealtimeProvider]) -> RealtimeAgentApp:
    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    app.agent_core = OmniRealtimeAgentCore(
        output_service=app.output_service,
        recorder=app.recorder,
        control_service=app.control_service,
        omni_config=RealtimeProviderConfig(provider="fake", model="fake-omni"),
        provider_factory=lambda config: _new_fake(config, instances),
    )
    app.vision_agent_core = app.agent_core
    app.audio_pipeline.agent_core = app.agent_core
    return app


def _new_fake(config: RealtimeProviderConfig, instances: list[FakeRealtimeProvider]) -> FakeRealtimeProvider:
    fake = FakeRealtimeProvider(config)
    instances.append(fake)
    return fake


class FakeAssetService:
    """测试用资产服务。

    主要功能：模拟 `sensor.rgb` 单帧请求，每次返回同一个本地 JPEG 文件引用。
    主要属性：`request_count` 记录服务端向端侧请求视觉帧的次数。
    """

    def __init__(self, image_path: Path) -> None:
        self.image_path = image_path
        self.request_count = 0
        self.query_count = 0
        self.requests: list[dict] = []
        self.claims: list[dict] = []

    def request_asset(self, **kwargs) -> AssetRef:
        """返回一帧本地测试图片。"""

        self.request_count += 1
        self.requests.append(dict(kwargs))
        return AssetRef(
            asset_id=f"asset-test-{self.request_count}",
            user_id=str(kwargs.get("user_id") or "user-001"),
            session_id=str(kwargs.get("session_id") or "sess-001"),
            stream_type=str(kwargs.get("stream_type") or "sensor.rgb"),
            mime_type="image/jpeg",
            created_at_ms=int(time.time() * 1000),
            uri=str(self.image_path),
            size_bytes=self.image_path.stat().st_size,
            metadata={"request_count": self.request_count},
        )

    def query_assets(self, **kwargs) -> list[AssetRef]:
        """模拟 continuous RGB stream 已经写入最新一帧。"""

        self.query_count += 1
        return [
            AssetRef(
                asset_id=f"asset-query-{self.query_count}",
                user_id=str(kwargs.get("user_id") or "user-001"),
                session_id="dev-web",
                stream_type=str(kwargs.get("stream_type") or "sensor.rgb"),
                mime_type="image/jpeg",
                created_at_ms=int(time.time() * 1000),
                uri=str(self.image_path),
                size_bytes=self.image_path.stat().st_size,
                metadata={"query_count": self.query_count},
            )
        ]

    def claim_photo_asset(self, **kwargs):
        """记录 Omni 视觉帧 claim。"""

        self.claims.append(dict(kwargs))
        return types.SimpleNamespace(ok=True, reason="", claim=types.SimpleNamespace(claim_id=f"claim-{len(self.claims)}"))

    def get_asset_payload(self, asset_id: str) -> bytes | None:
        """模拟内存 payload 缺失，测试磁盘回退路径。"""

        _ = asset_id
        return None


def test_realtime_append_audio_does_not_require_final_and_opens_speaker_stream(tmp_path) -> None:
    """测试目标：验证 realtime core 不依赖浏览器发送 final 即可处理音频。

    测试方法：注入 fake Omni adapter，发送 final=False 的 sensor.mic chunk。
    预期结果：fake 收到 append，Output Service 打开 actuator.speaker 并下发音频。
    """
    instances: list[FakeRealtimeProvider] = []
    app = _realtime_app(tmp_path, instances)
    connection = Connection("dev-web")
    register_speaker(app, connection)
    handle = app.open_input_stream(user_id="user-001", producer_id="dev-web")

    app.write_input_chunk(
        StreamChunk(
            user_id="user-001",
            session_id=handle.session_id,
            stream_id=handle.stream_id,
            stream_type="sensor.mic",
            seq=0,
            payload=b"\x00\x00" * 320,
            final=False,
        )
    )

    assert len(instances) == 1
    assert instances[0].appended[0].final is False
    assert connection.chunks
    assert connection.chunks[0].stream_type == "actuator.speaker"
    assert connection.chunks[0].sample_rate == 24000


def test_omni_audio_done_closes_current_output_stream(tmp_path) -> None:
    """测试目标：验证 provider audio done 会关闭当前 output stream。

    测试方法：先 append 一片音频触发 output stream，再让 fake provider 发送 done。
    预期结果：端侧收到 `stream.output.finish.requested`。
    """
    instances: list[FakeRealtimeProvider] = []
    app = _realtime_app(tmp_path, instances)
    connection = Connection("dev-web")
    register_speaker(app, connection)
    handle = app.open_input_stream(user_id="user-001", producer_id="dev-web")

    app.write_input_chunk(
        StreamChunk(
            user_id="user-001",
            session_id=handle.session_id,
            stream_id=handle.stream_id,
            stream_type="sensor.mic",
            seq=0,
            payload=b"\x00\x00" * 320,
            final=False,
        )
    )
    instances[0].emit_done()

    assert any(event.event_name == "stream.output.finish.requested" for event in connection.events)
    model_events = (tmp_path / "runs" / "user-001" / handle.session_id / "model-events.jsonl").read_text()
    assert "assistant_audio.done" in model_events


def test_realtime_provider_speech_started_publishes_control_event_after_output_finish(tmp_path) -> None:
    """测试目标：验证 Omni speech_started 不依赖服务器 output stream 仍处于 active。

    测试方法：先让 fake provider 完成一段输出，再模拟 provider 发现用户开始说话。
    预期结果：端侧仍收到 `audio.speech.started`；因为当前没有正在生成或播放的回复，
    provider 不应被取消，也不应把下一轮 response 提前标记为打断。
    """

    instances: list[FakeRealtimeProvider] = []
    app = _realtime_app(tmp_path, instances)
    connection = Connection("dev-web")
    register_speaker(app, connection)
    handle = app.open_input_stream(user_id="user-001", producer_id="dev-web")

    app.write_input_chunk(
        StreamChunk(
            user_id="user-001",
            session_id=handle.session_id,
            stream_id=handle.stream_id,
            stream_type="sensor.mic",
            seq=0,
            payload=b"\x00\x00" * 320,
            final=False,
        )
    )
    instances[0].emit_done()
    finish_event = next(event for event in connection.events if event.event_name == "stream.output.finish.requested")
    app.publish_control_event(
        Event(
            event_name="stream.output.closed",
            user_id="user-001",
            producer_id="dev-web",
            session_id=handle.session_id,
            stream_id=finish_event.stream_id,
            stream_type="actuator.speaker",
            payload={"reason": "test_endpoint_drain_done"},
        )
    )
    connection.events.clear()

    assert instances[0].callbacks is not None
    instances[0].callbacks.provider_event(
        {
            "event": "omni.input_audio_buffer.speech_started",
            "provider": "fake",
            "model": "fake-omni",
        }
    )

    event_names = [event.event_name for event in connection.events]
    assert "audio.speech.started" in event_names
    assert instances[0].cancelled is False
    agent_events_text = (tmp_path / "runs" / "user-001" / handle.session_id / "agent-events.jsonl").read_text(
        encoding="utf-8"
    )
    assert "omni.provider_speech_started.no_active_response" in agent_events_text
    assert "omni.response.marked_interrupted" not in agent_events_text


def test_realtime_provider_speech_stopped_publishes_control_event(tmp_path) -> None:
    """测试目标：验证 Omni speech_stopped 会发布统一端侧事件。

    测试方法：打开 fake realtime 会话后直接模拟 provider speech_stopped。
    预期结果：端侧收到 `audio.speech.stopped`，payload 中保留上行 stream_id。
    """

    instances: list[FakeRealtimeProvider] = []
    app = _realtime_app(tmp_path, instances)
    connection = Connection("dev-web")
    register_speaker(app, connection)
    handle = app.open_input_stream(user_id="user-001", producer_id="dev-web")

    app.write_input_chunk(
        StreamChunk(
            user_id="user-001",
            session_id=handle.session_id,
            stream_id=handle.stream_id,
            stream_type="sensor.mic",
            seq=0,
            payload=b"\x00\x00" * 320,
            final=False,
        )
    )
    connection.events.clear()

    assert instances[0].callbacks is not None
    instances[0].callbacks.provider_event(
        {
            "event": "omni.input_audio_buffer.speech_stopped",
            "provider": "fake",
            "model": "fake-omni",
        }
    )

    stopped = [event for event in connection.events if event.event_name == "audio.speech.stopped"]
    assert stopped
    assert stopped[0].payload.get("stream_id") == handle.stream_id


def test_realtime_interrupt_cancels_provider_and_output(tmp_path) -> None:
    """测试目标：验证用户打断会取消 provider 响应和当前播放。

    测试方法：fake provider 先输出音频，再发布 `control.user.interrupt.detected`。
    预期结果：fake cancel 被调用，端侧收到 output cancel 事件。
    """
    instances: list[FakeRealtimeProvider] = []
    app = _realtime_app(tmp_path, instances)
    connection = Connection("dev-web")
    register_speaker(app, connection)
    handle = app.open_input_stream(user_id="user-001", producer_id="dev-web")
    app.write_input_chunk(
        StreamChunk(
            user_id="user-001",
            session_id=handle.session_id,
            stream_id=handle.stream_id,
            stream_type="sensor.mic",
            seq=0,
            payload=b"\x00\x00" * 320,
            final=False,
        )
    )

    app.publish_control_event(
        Event(
            event_name="control.user.interrupt.detected",
            user_id="user-001",
            producer_id="dev-web",
            session_id=handle.session_id,
            payload={"reason": "test_interrupt"},
        )
    )

    assert instances[0].cancelled is True
    assert any(event.event_name == "stream.output.cancel.requested" for event in connection.events)


def test_realtime_provider_speech_started_cancels_active_output(tmp_path) -> None:
    """测试目标：验证 Omni provider 的 speech_started 事件能停止当前播放。

    测试方法：让 fake realtime provider 先输出一片未完成音频，再模拟
    `omni.input_audio_buffer.speech_started`。
    预期结果：provider cancel 被调用，端侧收到 output cancel 事件。
    """

    instances: list[FakeRealtimeProvider] = []
    app = _realtime_app(tmp_path, instances)
    connection = Connection("dev-web")
    register_speaker(app, connection)
    core = app.agent_core

    core.append_audio_event(
        StreamChunk(
            user_id="user-001",
            session_id="dev-web",
            stream_id="stream-mic-dev-web",
            stream_type="sensor.mic",
            seq=0,
            payload=b"\x01\x02",
        )
    )
    assert app.output_service.active_output_stream_id("user-001", "dev-web") is not None

    core._record_provider_event(
        user_id="user-001",
        session_id="dev-web",
        record={"event": "omni.input_audio_buffer.speech_started", "provider": "fake"},
    )

    event_names = [event.event_name for event in connection.events]
    assert instances[0].cancelled is True
    assert "audio.speech.started" in event_names
    assert "stream.output.cancel.requested" in event_names
    assert "stream.output.cancelled" not in event_names
    chunks_before_late_delta = len(connection.chunks)
    assert instances[0].callbacks is not None
    instances[0].callbacks.audio_delta(
        b"\x02\x00" * 480,
        StreamFormat(codec="pcm16le", sample_rate=24000, channels=1, chunk_ms=20),
        {"provider": "fake", "model": "fake-omni"},
    )
    instances[0].callbacks.audio_done({"provider": "fake", "model": "fake-omni"})
    assert len(connection.chunks) == chunks_before_late_delta
    agent_events_text = (tmp_path / "runs" / "user-001" / "dev-web" / "agent-events.jsonl").read_text(encoding="utf-8")
    assert "omni.provider_speech_started.interrupt" in agent_events_text
    assert "omni.response.audio_delta_ignored_after_interrupt" in agent_events_text
    assert "omni.response.audio_done_ignored_after_interrupt" in agent_events_text


def test_realtime_provider_speech_started_does_not_cancel_without_active_response(tmp_path) -> None:
    """测试目标：验证用户正常开始说话时不会提前取消后续回复。

    测试方法：只建立上行 realtime 会话，不让 fake provider 先创建 response，直接模拟
    `omni.input_audio_buffer.speech_started`。
    预期结果：provider cancel 不被调用，runs 中记录当前没有可打断的 active response。
    """

    instances: list[FakeRealtimeProvider] = []
    app = _realtime_app(tmp_path, instances)
    connection = Connection("dev-web")
    register_speaker(app, connection)
    core = app.agent_core
    session_id = "dev-web"
    core.open(user_id="user-001", session_id=session_id)

    assert app.output_service.active_output_stream_id("user-001", session_id) is None

    core._record_provider_event(
        user_id="user-001",
        session_id=session_id,
        record={"event": "omni.input_audio_buffer.speech_started", "provider": "fake"},
    )

    assert instances[0].cancelled is False
    assert any(event.event_name == "audio.speech.started" for event in connection.events)
    agent_events_text = (tmp_path / "runs" / "user-001" / session_id / "agent-events.jsonl").read_text(
        encoding="utf-8"
    )
    assert "omni.provider_speech_started.no_active_response" in agent_events_text
    assert "omni.response.marked_interrupted" not in agent_events_text


def test_realtime_provider_speech_started_cancels_active_response_without_output(tmp_path) -> None:
    """测试目标：验证没有下行音频但 response 正在生成时仍能被用户打断。

    测试方法：建立 fake realtime 会话，先模拟 provider 创建 response，再模拟
    `omni.input_audio_buffer.speech_started`。
    预期结果：provider cancel 被调用，runs 中记录当前 response 已被打断。
    """

    instances: list[FakeRealtimeProvider] = []
    app = _realtime_app(tmp_path, instances)
    connection = Connection("dev-web")
    register_speaker(app, connection)
    core = app.agent_core
    session_id = "dev-web"
    core.open(user_id="user-001", session_id=session_id)

    assert instances[0].callbacks is not None
    instances[0].callbacks.provider_event(
        {"event": "omni.response.created", "provider": "fake", "response_id": "resp-active"}
    )
    assert app.output_service.active_output_stream_id("user-001", session_id) is None

    core._record_provider_event(
        user_id="user-001",
        session_id=session_id,
        record={"event": "omni.input_audio_buffer.speech_started", "provider": "fake"},
    )

    assert instances[0].cancelled is True
    assert any(event.event_name == "audio.speech.started" for event in connection.events)
    agent_events_text = (tmp_path / "runs" / "user-001" / session_id / "agent-events.jsonl").read_text(
        encoding="utf-8"
    )
    assert "omni.provider_speech_started.no_active_output" in agent_events_text
    assert "omni.response.marked_interrupted" in agent_events_text


def test_realtime_core_appends_rgb_frames_during_provider_vad_turn(tmp_path) -> None:
    """测试目标：验证 Omni Realtime 只在 provider VAD turn 内按需追加图片。

    测试方法：注入 fake realtime provider 和 fake asset service，先追加音频打开会话，
    确认不会立刻请求 RGB；再模拟 provider 上报 speech_started，等待后台线程请求单帧
    并 append 到 provider，最后关闭会话。
    预期结果：会话打开阶段不采集图片；speech_started 后通过 AssetService 请求当前
    单帧图片，runs 中记录视觉采样开始、追加和停止。
    """

    image_path = tmp_path / "frame.jpg"
    image_path.write_bytes(b"\xff\xd8fake-jpeg\xff\xd9")
    asset_service = FakeAssetService(image_path)
    instances: list[FakeRealtimeProvider] = []
    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    connection = Connection("dev-web")
    register_speaker_and_rgb(app, connection)
    core = OmniRealtimeAgentCore(
        control_service=app.control_service,
        asset_service=asset_service,  # type: ignore[arg-type]
        output_service=app.output_service,
        recorder=app.recorder,
        omni_config=RealtimeProviderConfig(
            provider="fake",
            model="fake-omni",
            visual_frame_interval_seconds=0.05,
            visual_frame_timeout_seconds=0.1,
        ),
        provider_factory=lambda config: _new_fake(config, instances),
        tool_gateway=app.tool_gateway,
    )

    core.append_audio_event(
        StreamChunk(
            user_id="user-001",
            session_id="dev-web",
            stream_id="stream-mic-dev-web",
            stream_type="sensor.mic",
            seq=0,
            payload=b"\x01\x02",
        )
    )
    time.sleep(0.1)
    assert asset_service.request_count == 0
    assert asset_service.query_count == 0
    assert not instances[0].images

    core._record_provider_event(
        user_id="user-001",
        session_id="dev-web",
        record={"event": "omni.input_audio_buffer.speech_started", "provider": "fake"},
    )
    deadline = time.time() + 1
    while time.time() < deadline and not instances[0].images:
        time.sleep(0.02)
    core.close("user-001", reason="test_done")

    assert asset_service.query_count == 0
    assert asset_service.request_count >= 1
    assert asset_service.requests[0]["freshness_seconds"] == 0.0
    assert asset_service.requests[0]["device_ids"] == ("dev-web",)
    assert asset_service.requests[0]["params"]["sample_count"] == 1
    assert asset_service.requests[0]["params"]["capture_reason"] == "realtime_video"
    assert asset_service.claims
    assert asset_service.claims[0]["consumer"] == "agent_inline"
    assert asset_service.claims[0]["reason"] == "realtime_video_append"
    assert instances[0].images
    assert instances[0].images[0][0] == image_path.read_bytes()
    assert instances[0].images[0][1]["frame_index"] == 0
    agent_events = (tmp_path / "runs" / "user-001" / "dev-web" / "agent-events.jsonl").read_text(encoding="utf-8")
    assert "omni.visual_sampler.started" in agent_events
    assert "omni.visual_frame.appended" in agent_events
    assert "omni.visual_sampler.stopped" in agent_events
    assert "omni.visual_stream.open.requested" not in agent_events


def test_realtime_visual_sampler_stops_when_no_rgb_device(tmp_path) -> None:
    """测试目标：验证音频设备没有在线 RGB 能力时，视觉采样不会持续刷请求。

    测试方法：只注册扬声器设备，不注册 `sensor.rgb` 能力；先追加一片来自该设备的
    音频，再模拟 provider 上报 `speech_started`，等待后台采样线程完成一次设备检查。
    预期结果：不会调用 AssetService 请求图片，runs 记录配对链路不可用并停止采样。
    """

    image_path = tmp_path / "frame.jpg"
    image_path.write_bytes(b"\xff\xd8fake-jpeg\xff\xd9")
    asset_service = FakeAssetService(image_path)
    instances: list[FakeRealtimeProvider] = []
    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    connection = Connection("dev-audio-only")
    register_speaker(app, connection)
    core = OmniRealtimeAgentCore(
        control_service=app.control_service,
        asset_service=asset_service,  # type: ignore[arg-type]
        output_service=app.output_service,
        recorder=app.recorder,
        omni_config=RealtimeProviderConfig(
            provider="fake",
            model="fake-omni",
            visual_frame_interval_seconds=0.05,
            visual_frame_timeout_seconds=0.1,
        ),
        provider_factory=lambda config: _new_fake(config, instances),
        tool_gateway=app.tool_gateway,
    )

    core.append_audio_event(
        StreamChunk(
            user_id="user-001",
            session_id="dev-audio-only",
            stream_id="stream-mic-audio-only",
            stream_type="sensor.mic",
            seq=0,
            payload=b"\x01\x02",
        )
    )
    core._record_provider_event(
        user_id="user-001",
        session_id="dev-audio-only",
        record={"event": "omni.input_audio_buffer.speech_started", "provider": "fake"},
    )
    deadline = time.time() + 1
    agent_events_path = tmp_path / "runs" / "user-001" / "dev-audio-only" / "agent-events.jsonl"
    while time.time() < deadline:
        agent_events = agent_events_path.read_text(encoding="utf-8") if agent_events_path.exists() else ""
        if (
            "omni.visual_sampler.paired_stream_unavailable" in agent_events
            and "omni.visual_sampler.stopped" in agent_events
        ):
            break
        time.sleep(0.02)

    agent_events = agent_events_path.read_text(encoding="utf-8")
    assert asset_service.request_count == 0
    assert not instances[0].images
    assert "omni.visual_sampler.paired_stream_unavailable" in agent_events
    assert "omni.visual_sampler.stopped" in agent_events


def test_realtime_visual_sampler_ignores_other_rgb_device(tmp_path) -> None:
    """测试目标：验证视觉采样不会使用同一用户下其他设备的 RGB 能力。

    测试方法：当前音频来自只支持扬声器的设备，同时注册另一台支持 RGB 的设备；
    模拟 `speech_started` 后等待采样线程检查配对链路。
    预期结果：采样停止，不调用 AssetService，也不会把另一台设备的图片拼进当前语音。
    """

    image_path = tmp_path / "frame.jpg"
    image_path.write_bytes(b"\xff\xd8fake-jpeg\xff\xd9")
    asset_service = FakeAssetService(image_path)
    instances: list[FakeRealtimeProvider] = []
    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    audio_connection = Connection("dev-audio-only")
    rgb_connection = Connection("dev-rgb")
    register_speaker(app, audio_connection)
    register_speaker_and_rgb(app, rgb_connection)
    core = OmniRealtimeAgentCore(
        control_service=app.control_service,
        asset_service=asset_service,  # type: ignore[arg-type]
        output_service=app.output_service,
        recorder=app.recorder,
        omni_config=RealtimeProviderConfig(
            provider="fake",
            model="fake-omni",
            visual_frame_interval_seconds=0.05,
            visual_frame_timeout_seconds=0.1,
        ),
        provider_factory=lambda config: _new_fake(config, instances),
        tool_gateway=app.tool_gateway,
    )

    core.append_audio_event(
        StreamChunk(
            user_id="user-001",
            session_id="dev-audio-only",
            stream_id="stream-mic-audio-only",
            stream_type="sensor.mic",
            seq=0,
            payload=b"\x01\x02",
        )
    )
    core._record_provider_event(
        user_id="user-001",
        session_id="dev-audio-only",
        record={"event": "omni.input_audio_buffer.speech_started", "provider": "fake"},
    )
    deadline = time.time() + 1
    agent_events_path = tmp_path / "runs" / "user-001" / "dev-audio-only" / "agent-events.jsonl"
    while time.time() < deadline:
        agent_events = agent_events_path.read_text(encoding="utf-8") if agent_events_path.exists() else ""
        if "omni.visual_sampler.paired_stream_unavailable" in agent_events:
            break
        time.sleep(0.02)

    assert asset_service.request_count == 0
    assert not instances[0].images
    assert not any(
        event.event_name == "stream.control.open.requested" and event.stream_type == "sensor.rgb"
        for event in rgb_connection.events
    )


def test_prepare_omni_realtime_image_compresses_large_jpeg() -> None:
    """测试目标：验证发送给 Omni Realtime 前会把大图压到安全大小。

    测试方法：用 OpenCV 构造一张高噪声 JPEG，大于 provider WebSocket 单帧安全阈值，
    调用服务端图片预处理函数。
    预期结果：返回 JPEG 小于 `OMNI_REALTIME_IMAGE_MAX_BYTES`，并带有压缩诊断信息。
    """

    import cv2  # type: ignore[import-not-found]
    import numpy as np  # type: ignore[import-not-found]

    rng = np.random.default_rng(42)
    image = rng.integers(0, 256, size=(1200, 1600, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    assert ok
    original = encoded.tobytes()
    assert len(original) > OMNI_REALTIME_IMAGE_MAX_BYTES

    prepared, metadata = _prepare_omni_realtime_image(original)

    assert len(prepared) <= OMNI_REALTIME_IMAGE_MAX_BYTES
    assert metadata["image_compressed"] is True
    assert metadata["original_image_bytes"] == len(original)


def test_realtime_provider_failure_suppresses_repeated_append_errors(tmp_path) -> None:
    """测试目标：验证 provider 失败后不再对同 session 继续 append。

    测试方法：注入会抛 `Connection is already closed.` 的 fake provider，连续写入两片
    mic chunk。
    预期结果：provider append 只调用一次，runs 只记录一次 `omni.session.failed`。
    """
    instances: list[FailingRealtimeProvider] = []
    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    app.agent_core = OmniRealtimeAgentCore(
        output_service=app.output_service,
        recorder=app.recorder,
        omni_config=RealtimeProviderConfig(provider="fake", model="fake-omni"),
        provider_factory=lambda config: _new_failing_fake(config, instances),
    )
    app.vision_agent_core = app.agent_core
    app.audio_pipeline.agent_core = app.agent_core
    connection = Connection("dev-web")
    register_speaker(app, connection)
    handle = app.open_input_stream(user_id="user-001", producer_id="dev-web")
    chunk = StreamChunk(
        user_id="user-001",
        session_id=handle.session_id,
        stream_id=handle.stream_id,
        stream_type="sensor.mic",
        seq=0,
        payload=b"\x00\x00" * 320,
        final=False,
    )

    app.write_input_chunk(chunk)
    app.write_input_chunk(StreamChunk(**{**chunk.__dict__, "seq": 1}))

    assert len(instances) == 1
    assert instances[0].append_calls == 1
    model_events = (tmp_path / "runs" / "user-001" / handle.session_id / "model-events.jsonl").read_text()
    assert model_events.count("omni.session.failed") == 1


def test_realtime_mode_uses_builtin_mock_provider_for_local_chain(tmp_path) -> None:
    """测试目标：验证 omni 模式具备稳定 mock provider 链路。

    测试方法：配置 `omni_provider=mock` 创建 app，发送一片 final mic chunk。
    预期结果：端侧收到 speaker chunk 和 close 事件，core events 记录 session 与响应事件。
    """

    app = RealtimeAgentApp(
        RealtimeAgentConfig(
            runs_root=str(tmp_path / "runs"),
            agent_mode="omni",
            omni_provider="mock",
            omni_model="mock-omni",
        )
    )
    connection = Connection("dev-web")
    register_speaker(app, connection)
    handle = app.open_input_stream(user_id="user-001", producer_id="dev-web")

    app.write_input_chunk(
        StreamChunk(
            user_id="user-001",
            session_id=handle.session_id,
            stream_id=handle.stream_id,
            stream_type="sensor.mic",
            seq=0,
            payload=b"\x00\x00" * 320,
            final=True,
        )
    )

    assert connection.chunks
    assert any(event.event_name == "stream.output.finish.requested" for event in connection.events)
    assert any(event.event == "session.opened" for event in app.agent_core.events())
    assert any(event.event == "mock_omni.input.committed" for event in app.agent_core.events())
    assert any(
        event.event == "agent.turn_state.changed" and event.payload.get("state") == "speaking"
        for event in app.agent_core.events()
    )
    model_events = (tmp_path / "runs" / "user-001" / handle.session_id / "model-events.jsonl").read_text()
    assert "agent.turn_state.changed" in model_events
    assert '"agent_core": "OmniRealtimeAgentCore"' in model_events
    assert '"modality": "omni"' in model_events


def test_realtime_commit_input_forwards_to_provider_and_records_event(tmp_path) -> None:
    """测试目标：验证 OmniRealtimeAgentCore 的公共 `commit_input` 接口。

    测试方法：注入 fake provider，先 append 音频打开会话，再显式 commit。
    预期结果：fake 输出 done，端侧收到 close 事件，core events 记录 input.committed。
    """

    instances: list[FakeRealtimeProvider] = []
    app = _realtime_app(tmp_path, instances)
    connection = Connection("dev-web")
    register_speaker(app, connection)
    handle = app.open_input_stream(user_id="user-001", producer_id="dev-web")
    app.write_input_chunk(
        StreamChunk(
            user_id="user-001",
            session_id=handle.session_id,
            stream_id=handle.stream_id,
            stream_type="sensor.mic",
            seq=0,
            payload=b"\x00\x00" * 320,
            final=False,
        )
    )

    app.agent_core.commit_input("user-001", handle.session_id, reason="unit_commit")

    assert any(event.event_name == "stream.output.finish.requested" for event in connection.events)
    assert any(event.event == "input.committed" for event in app.agent_core.events())


def test_realtime_open_records_equivalent_model_request_and_injects_tool_schema(tmp_path) -> None:
    """测试目标：验证 Omni Realtime 也有等价 model request 和工具 schema。

    测试方法：注册一个测试 Tool，注入 fake realtime provider，写入一片 mic chunk 打开会话。
    预期结果：provider config 收到扁平 function schema；视觉类工具被 realtime
    内联图片链路替代，不再暴露给 Omni。
    """

    instances: list[FakeRealtimeProvider] = []
    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    app.tool_registry.register(EchoRealtimeTool())
    app.tool_registry.register(CapturePhotoRealtimeTool())
    app.tool_registry.register(InterpretCurrentViewRealtimeExcludedTool())
    app.agent_core = OmniRealtimeAgentCore(
        output_service=app.output_service,
        recorder=app.recorder,
        omni_config=RealtimeProviderConfig(
            provider="fake",
            model="fake-omni",
            prompt="你是测试用 Omni 助手。",
        ),
        provider_factory=lambda config: _new_fake(config, instances),
        tool_gateway=app.tool_gateway,
    )
    app.audio_pipeline.agent_core = app.agent_core
    connection = Connection("dev-web")
    register_speaker(app, connection)
    handle = app.open_input_stream(user_id="user-001", producer_id="dev-web")

    app.write_input_chunk(
        StreamChunk(
            user_id="user-001",
            session_id=handle.session_id,
            stream_id=handle.stream_id,
            stream_type="sensor.mic",
            seq=0,
            payload=b"\x00\x00" * 320,
            final=False,
        )
    )

    tool_names = {tool["name"] for tool in instances[0].config.tools}
    assert "echo_realtime" in tool_names
    assert "capture_photo" not in tool_names
    assert "interpret_current_view" not in tool_names
    model_request = (tmp_path / "runs" / "user-001" / handle.session_id / "model-request.json").read_text(
        encoding="utf-8"
    )
    assert "agent_core_omni_audio" in model_request
    assert "input_audio_stream" in model_request
    assert "你是测试用 Omni 助手。" in model_request
    assert REALTIME_TOOL_CALL_PROMPT_RULE in instances[0].config.prompt
    assert REALTIME_TOOL_CALL_PROMPT_RULE in model_request
    assert "echo_realtime" in model_request
    assert "capture_photo" not in model_request
    assert "interpret_current_view" not in model_request


def test_realtime_open_loads_device_message_history_as_flat_messages(tmp_path) -> None:
    """测试目标：验证 Realtime 会话启动时把未压缩历史按 role 平铺到 messages。

    测试方法：预先写入 `messages.jsonl`，再打开 fake Realtime provider 会话。
    预期结果：system content 不包含 active 历史；`model-request.json` 的 messages
    依次包含 system、历史 user/assistant 和当前 input_audio_stream。
    """

    instances: list[FakeRealtimeProvider] = []
    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    user_id = "user-001"
    device_id = "dev-web"
    messages_path = tmp_path / "runs" / user_id / device_id / "messages.jsonl"
    messages_path.parent.mkdir(parents=True, exist_ok=True)
    messages_path.write_text(
        "\n".join(
            json.dumps(item, ensure_ascii=False)
            for item in [
                {"session_id": device_id, "role": "user", "content": "我刚才想去南门。"},
                {"session_id": device_id, "role": "assistant", "content": "我会继续按南门方向引导。"},
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    app.agent_core = OmniRealtimeAgentCore(
        output_service=app.output_service,
        recorder=app.recorder,
        control_service=app.control_service,
        omni_config=RealtimeProviderConfig(provider="fake", model="fake-omni", prompt="你是测试用 Omni 助手。"),
        provider_factory=lambda config: _new_fake(config, instances),
        tool_gateway=app.tool_gateway,
        max_context_messages=10,
    )
    app.audio_pipeline.agent_core = app.agent_core

    app.agent_core.open(user_id=user_id, session_id=device_id)

    request = json.loads((tmp_path / "runs" / user_id / device_id / "model-request.json").read_text(encoding="utf-8"))
    assert "我刚才想去南门。" not in instances[0].config.prompt
    assert "我刚才想去南门。" not in request["messages"][0]["content"]
    assert request["messages"][1] == {"role": "user", "content": "我刚才想去南门。"}
    assert request["messages"][2] == {"role": "assistant", "content": "我会继续按南门方向引导。"}
    assert request["messages"][3]["content"][0]["type"] == "input_audio_stream"
    assert request["active_history_message_count"] == 2
    assert request["active_history_injected_to"] == "messages"


def test_realtime_open_keeps_summary_in_system_and_active_history_as_messages(tmp_path) -> None:
    """测试目标：验证 Realtime 把摘要放 system，把 active 历史平铺到 messages。

    测试方法：先压缩一批历史消息，再打开 fake Realtime provider 会话。
    预期结果：prompt/system 包含 summary；保留的 active 历史只出现在后续
    user/assistant messages 中。
    """

    instances: list[FakeRealtimeProvider] = []
    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    app.conversation_memory.summarizer = FixedSummaryAgent()
    user_id = "user-summary"
    device_id = "dev-summary"
    for index in range(8):
        app.control_service.append_message(
            user_id,
            {
                "session_id": device_id,
                "role": "user" if index % 2 == 0 else "assistant",
                "content": f"实时压缩前消息 {index}",
                "created_at": 1_700_000_000 + index,
            },
        )
    app.control_service.compact_messages_if_needed(user_id=user_id, session_id=device_id, threshold=6, keep_latest=2)
    app.agent_core = OmniRealtimeAgentCore(
        output_service=app.output_service,
        recorder=app.recorder,
        control_service=app.control_service,
        omni_config=RealtimeProviderConfig(provider="fake", model="fake-omni", prompt="你是测试用 Omni 助手。"),
        provider_factory=lambda config: _new_fake(config, instances),
        tool_gateway=app.tool_gateway,
        max_context_messages=10,
    )

    app.agent_core.open(user_id=user_id, session_id=device_id)

    request = json.loads((tmp_path / "runs" / user_id / device_id / "model-request.json").read_text(encoding="utf-8"))
    prompt = instances[0].config.prompt
    assert "更早历史对话的压缩摘要" in prompt
    assert "实时压缩前消息 0 已被归档" in prompt
    assert "实时压缩前消息 6" not in prompt
    assert request["messages"][0]["content"] == prompt
    assert request["messages"][1]["content"] == "实时压缩前消息 6"
    assert request["messages"][2]["content"] == "实时压缩前消息 7"
    assert request["messages"][3]["content"][0]["type"] == "input_audio_stream"


def test_qwen_omni_tool_result_is_injected_back_to_conversation() -> None:
    """测试目标：验证 Qwen Omni 工具结果会回填到同一条 Realtime 会话。

    测试方法：绕过网络注入 fake conversation，直接触发 provider tool done 事件。
    预期结果：conversation 收到 `function_call_output`，并在原 response.done 后继续创建音频响应。
    """

    from realtime_agent.agent_core.omni import QwenOmniRealtimeAdapter

    class FakeConversation:
        """记录 provider adapter 写回的会话操作。"""

        def __init__(self) -> None:
            self.items = []
            self.responses = []

        def create_item(self, item: dict) -> None:
            self.items.append(item)

        def create_response(self, **kwargs) -> None:
            self.responses.append(kwargs)

    records = []
    conversation = FakeConversation()
    provider = QwenOmniRealtimeAdapter(RealtimeProviderConfig(provider="qwen", model="fake-omni", prompt="继续回答"))
    provider._conversation = conversation
    provider._output_modalities = ["text", "audio"]
    provider._callbacks = RealtimeProviderCallbacks(
        audio_delta=lambda audio, fmt, metadata: None,
        audio_done=lambda metadata: None,
        provider_event=records.append,
        error=lambda message, record: records.append({"event": "error", "message": message, **record}),
        tool_call_done=lambda record: {"tool_call_id": record["tool_call_id"], "name": record["name"], "ok": True},
    )

    provider._handle_provider_event(
        {
            "type": "response.function_call_arguments.done",
            "call_id": "call-001",
            "name": "echo_realtime",
            "arguments": "{\"value\":\"ok\"}",
        }
    )

    assert conversation.items[0]["type"] == "function_call_output"
    assert conversation.items[0]["call_id"] == "call-001"
    assert "\"ok\": true" in conversation.items[0]["output"]
    assert conversation.responses == []
    assert any(record.get("event") == "omni.tool_followup_response.deferred" for record in records)
    provider._handle_provider_event({"type": "response.done", "status": "completed"})
    assert conversation.responses[0]["instructions"] == "继续回答"
    assert any(record.get("event") == "omni.tool_followup_response.created" for record in records)
    assert any(record.get("event") == "omni.tool_result.ready" for record in records)


def test_qwen_omni_realtime_provider_enforces_concurrency_limit(monkeypatch) -> None:
    """测试目标：确认真实 Realtime provider 连接入口会按配置限制并发会话。

    测试方法：注入 fake DashScope SDK，把并发上限设为 1；先打开一个会话，再尝试
    打开第二个同 provider / model / endpoint 会话。
    预期结果：第二个会话在建立 provider 连接前被拒绝；第一个会话关闭后槽位释放，
    后续新会话可以正常打开。
    """

    from realtime_agent.agent_core.omni import QwenOmniRealtimeAdapter

    class FakeAudioFormat:
        """测试用音频格式常量。"""

        PCM_16000HZ_MONO_16BIT = "pcm16le/16000/mono"
        PCM_24000HZ_MONO_16BIT = "pcm16le/24000/mono"

    class FakeMultiModality:
        """测试用输出模态常量。"""

        TEXT = "text"
        AUDIO = "audio"

    class FakeCallback:
        """测试用 DashScope callback 基类。"""

    class FakeConversation:
        """测试用 DashScope conversation。

        主要功能：记录连接和关闭状态，`update_session()` 时主动回调
        `session.updated`，让 adapter 的 open 流程完成。
        主要属性：`instances` 保存已创建的 conversation，便于判断限流前是否访问 provider。
        """

        instances: list["FakeConversation"] = []

        def __init__(self, *, model: str, callback: FakeCallback, url: str, api_key: str) -> None:
            self.model = model
            self.callback = callback
            self.url = url
            self.api_key = api_key
            self.connected = False
            self.closed = False
            FakeConversation.instances.append(self)

        def connect(self) -> None:
            """模拟 provider WebSocket 连接成功。"""

            self.connected = True

        def update_session(self, **kwargs) -> None:
            """模拟 provider session 更新成功。"""

            self.session_kwargs = kwargs
            self.callback.on_event({"type": "session.updated"})

        def close(self) -> None:
            """模拟 provider 会话关闭。"""

            self.closed = True

    dashscope_module = types.ModuleType("dashscope")
    dashscope_audio_module = types.ModuleType("dashscope.audio")
    qwen_omni_module = types.ModuleType("dashscope.audio.qwen_omni")
    qwen_omni_module.AudioFormat = FakeAudioFormat
    qwen_omni_module.MultiModality = FakeMultiModality
    qwen_omni_module.OmniRealtimeCallback = FakeCallback
    qwen_omni_module.OmniRealtimeConversation = FakeConversation
    monkeypatch.setitem(sys.modules, "dashscope", dashscope_module)
    monkeypatch.setitem(sys.modules, "dashscope.audio", dashscope_audio_module)
    monkeypatch.setitem(sys.modules, "dashscope.audio.qwen_omni", qwen_omni_module)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")

    records: list[dict] = []
    callbacks = RealtimeProviderCallbacks(
        audio_delta=lambda audio, fmt, metadata: None,
        audio_done=lambda metadata: None,
        provider_event=records.append,
        error=lambda message, record: records.append({"event": "error", "message": message, **record}),
    )
    config = RealtimeProviderConfig(
        provider="qwen",
        model="fake-omni-limit-test",
        websocket_url="wss://example.invalid/realtime-limit-test",
        max_concurrent_sessions=1,
    )

    first = QwenOmniRealtimeAdapter(config)
    first.open(user_id="user-001", session_id="session-001", callbacks=callbacks)
    assert len(FakeConversation.instances) == 1
    assert FakeConversation.instances[0].connected is True

    second = QwenOmniRealtimeAdapter(config)
    try:
        second.open(user_id="user-002", session_id="session-002", callbacks=callbacks)
        raise AssertionError("第二个并发会话应该被 provider limiter 拒绝")
    except RealtimeProviderConcurrencyLimitError:
        pass

    assert len(FakeConversation.instances) == 1
    assert any(record.get("event") == "omni.provider.concurrency_limited" for record in records)

    first.close(user_id="user-001", reason="test_done")
    second.open(user_id="user-002", session_id="session-002", callbacks=callbacks)
    assert len(FakeConversation.instances) == 2
    second.close(user_id="user-002", reason="test_done")


def test_qwen_omni_tool_failure_followup_instructions_force_failure_ack() -> None:
    """测试目标：验证 Realtime 工具失败后 follow-up 明确约束模型承认失败。

    测试方法：模拟 task_runtime_manager 启动任务失败，触发工具结果回填和 response.done。
    预期结果：创建 follow-up response 的 instructions 包含失败原因，并禁止声称成功。
    """

    from realtime_agent.agent_core.omni import QwenOmniRealtimeAdapter

    class FakeConversation:
        """记录 provider adapter 写回的会话操作。"""

        def __init__(self) -> None:
            self.items = []
            self.responses = []

        def create_item(self, item: dict) -> None:
            self.items.append(item)

        def create_response(self, **kwargs) -> None:
            self.responses.append(kwargs)

    records = []
    conversation = FakeConversation()
    provider = QwenOmniRealtimeAdapter(RealtimeProviderConfig(provider="qwen", model="fake-omni", prompt="基础指令"))
    provider._conversation = conversation
    provider._output_modalities = ["text", "audio"]
    provider._callbacks = RealtimeProviderCallbacks(
        audio_delta=lambda audio, fmt, metadata: None,
        audio_done=lambda metadata: None,
        provider_event=records.append,
        error=lambda message, record: records.append({"event": "error", "message": message, **record}),
    )

    provider._submit_tool_result(
        call_id="call-failed-task",
        result={
            "tool_call_id": "call-failed-task",
            "name": "task_runtime_manager",
            "ok": False,
            "data": None,
            "message": "任务启动失败：unknown task: timer",
            "error": {"code": "not_found", "message": "unknown task: timer", "retryable": False, "details": {}},
            "meta": {"operation": "task_start", "requested_task_type": "timer", "resolved_task_type": "timer"},
        },
    )
    provider._handle_provider_event({"type": "response.done", "response": {"status": "completed"}})

    instructions = conversation.responses[0]["instructions"]
    assert "基础指令" in instructions
    assert "操作失败" in instructions
    assert "unknown task: timer" in instructions
    assert "任务没有启动" in instructions
    assert "不能声称操作已经执行成功" in instructions
    assert "不要向用户复述工具名" in instructions


def test_qwen_omni_final_audio_chunk_commits_input_boundary() -> None:
    """测试目标：验证 Qwen Omni 收到 endpoint final chunk 时只提交输入边界。

    测试方法：给 `QwenOmniRealtimeAdapter` 注入 fake conversation，发送一片
    `final=true` 的 sensor.mic chunk。
    预期结果：conversation 收到音频 append，并调用 DashScope SDK 的 `commit()`。
    回答创建由 provider 的 VAD/回合事件驱动，避免重复创建响应。
    """

    from realtime_agent.agent_core.omni import QwenOmniRealtimeAdapter

    class FakeConversation:
        """记录 Omni 输入追加和提交调用。"""

        def __init__(self) -> None:
            self.audios = []
            self.commits = 0

        def append_audio(self, audio_base64: str) -> None:
            self.audios.append(audio_base64)

        def commit(self) -> None:
            self.commits += 1

    records = []
    conversation = FakeConversation()
    provider = QwenOmniRealtimeAdapter(RealtimeProviderConfig(provider="qwen", model="fake-omni"))
    provider._conversation = conversation
    provider._callbacks = RealtimeProviderCallbacks(
        audio_delta=lambda audio, fmt, metadata: None,
        audio_done=lambda metadata: None,
        provider_event=records.append,
        error=lambda message, record: records.append({"event": "error", "message": message, **record}),
    )

    provider.append_audio(
        StreamChunk(
            user_id="user-001",
            session_id="sess-001",
            stream_id="stream-mic",
            stream_type="sensor.mic",
            payload=b"\x01\x02",
            codec="pcm16le",
            sample_rate=16000,
            channels=1,
            duration_ms=20,
            seq=1,
            final=True,
        )
    )

    assert conversation.audios == ["AQI="]
    assert conversation.commits == 1
    assert any(record.get("event") == "omni.input.committed" for record in records)


def test_qwen_omni_capture_photo_appends_image_bytes(tmp_path) -> None:
    """测试目标：验证 Omni Realtime 的 `capture_photo` 工具成功后会追加真实图片。

    测试方法：构造包含本地 JPEG 路径的工具结果，注入 fake conversation 后调用
    `_submit_tool_result()`。
    预期结果：conversation 收到 `function_call_output`、上一轮用户音频、base64
    图片和 commit，由 provider 在提交后自动基于“用户问题音频 + 图片”响应。
    """

    from realtime_agent.agent_core.omni import QwenOmniRealtimeAdapter

    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"\xff\xd8browser-photo\xff\xd9")

    class FakeConversation:
        """记录 provider adapter 写回的会话操作。"""

        def __init__(self) -> None:
            self.items = []
            self.audios = []
            self.videos = []
            self.commits = 0
            self.responses = []
            self.operations = []

        def create_item(self, item: dict) -> None:
            self.items.append(item)
            self.operations.append(("item", item["type"]))

        def append_video(self, image_base64: str) -> None:
            self.videos.append(image_base64)
            self.operations.append(("video", image_base64))

        def append_audio(self, audio_base64: str) -> None:
            self.audios.append(audio_base64)
            self.operations.append(("audio", audio_base64))

        def commit(self) -> None:
            self.commits += 1
            self.operations.append(("commit", None))

        def create_response(self, **kwargs) -> None:
            self.responses.append(kwargs)

    records = []
    conversation = FakeConversation()
    provider = QwenOmniRealtimeAdapter(RealtimeProviderConfig(provider="qwen", model="fake-omni", prompt="结合图片回答"))
    provider._conversation = conversation
    provider._output_modalities = ["text", "audio"]
    provider._callbacks = RealtimeProviderCallbacks(
        audio_delta=lambda audio, fmt, metadata: None,
        audio_done=lambda metadata: None,
        provider_event=records.append,
        error=lambda message, record: records.append({"event": "error", "message": message, **record}),
        replay_audio_for_tool_result=lambda result: [b"\x01\x02", b"\x03\x04"],
    )

    provider._submit_tool_result(
        call_id="call-photo",
        result={
            "tool_call_id": "call-photo",
            "name": "capture_photo",
            "ok": True,
            "data": {"storage_uri": str(image_path), "mime_type": "image/jpeg"},
            "message": "已完成一次抓拍。",
            "error": None,
            "meta": {},
        },
    )

    assert conversation.items[0]["type"] == "function_call_output"
    assert conversation.audios == ["AQI=", "AwQ="]
    assert conversation.videos == ["/9hicm93c2VyLXBob3Rv/9k="]
    assert conversation.operations == [
        ("item", "function_call_output"),
        ("audio", "AQI="),
        ("audio", "AwQ="),
        ("video", "/9hicm93c2VyLXBob3Rv/9k="),
        ("commit", None),
    ]
    assert conversation.commits == 1
    assert conversation.responses == []
    assert provider._pending_tool_followup_response is None
    append_record = next(record for record in records if record.get("event") == "omni.capture_photo.image_appended")
    assert append_record["image_path"] == str(image_path.resolve())
    assert append_record["image_sha256"] == "4c84c82bf54f47daa25a64cc46cb553c7c073ecc64c9f8b40287301cc3bf3407"
    assert append_record["replayed_audio_bytes"] == 4
    assert append_record["replayed_audio_chunk_count"] == 2
    assert append_record["committed"] is True
    assert append_record["response_create"] == "provider_auto_after_commit"


def test_qwen_omni_capture_photo_accepts_uri_field_for_image_path(tmp_path) -> None:
    """测试目标：验证 capture_photo 返回 `data.uri` 时也能把图片追加回 Omni。

    测试方法：构造只包含 `data.uri` 的成功工具结果，调用 `_submit_tool_result()`。
    预期结果：provider 能找到本地图片文件，不再报 `missing_image`，并正常追加图片。
    """

    from realtime_agent.agent_core.omni import QwenOmniRealtimeAdapter

    image_path = tmp_path / "photo-uri.jpg"
    image_path.write_bytes(b"\xff\xd8browser-photo-uri\xff\xd9")

    class FakeConversation:
        """记录 provider adapter 写回的会话操作。"""

        def __init__(self) -> None:
            self.items = []
            self.audios = []
            self.videos = []
            self.commits = 0
            self.responses = []

        def create_item(self, item: dict) -> None:
            self.items.append(item)

        def append_video(self, image_base64: str) -> None:
            self.videos.append(image_base64)

        def append_audio(self, audio_base64: str) -> None:
            self.audios.append(audio_base64)

        def commit(self) -> None:
            self.commits += 1

        def create_response(self, **kwargs) -> None:
            self.responses.append(kwargs)

    records = []
    conversation = FakeConversation()
    provider = QwenOmniRealtimeAdapter(RealtimeProviderConfig(provider="qwen", model="fake-omni", prompt="结合图片回答"))
    provider._conversation = conversation
    provider._output_modalities = ["text", "audio"]
    provider._callbacks = RealtimeProviderCallbacks(
        audio_delta=lambda audio, fmt, metadata: None,
        audio_done=lambda metadata: None,
        provider_event=records.append,
        error=lambda message, record: records.append({"event": "error", "message": message, **record}),
        replay_audio_for_tool_result=lambda result: [b"\x01\x02"],
    )

    provider._submit_tool_result(
        call_id="call-photo-uri",
        result={
            "tool_call_id": "call-photo-uri",
            "name": "capture_photo",
            "ok": True,
            "data": {"uri": str(image_path), "mime_type": "image/jpeg"},
            "message": "已完成一次抓拍。",
            "error": None,
            "meta": {},
        },
    )

    assert conversation.items[0]["type"] == "function_call_output"
    assert conversation.audios == ["AQI="]
    assert conversation.videos == ["/9hicm93c2VyLXBob3RvLXVyaf/Z"]
    assert conversation.commits == 1
    assert provider._pending_tool_followup_response is None
    assert not any(record.get("event") == "omni.capture_photo.image_append.missing_image" for record in records)


def test_qwen_omni_duplicate_tool_done_is_ignored() -> None:
    """测试目标：验证 Qwen Omni 同一个工具调用只执行并回填一次。

    测试方法：模拟 provider 先发送 `function_call_arguments.done`，随后又发送
    同 call_id 的 `output_item.done`。
    预期结果：工具回调和 conversation item 只发生一次，follow-up response 只创建一次，并记录重复忽略事件。
    """

    from realtime_agent.agent_core.omni import QwenOmniRealtimeAdapter

    class FakeConversation:
        """记录 provider adapter 写回的会话操作。"""

        def __init__(self) -> None:
            self.items = []
            self.responses = []

        def create_item(self, item: dict) -> None:
            self.items.append(item)

        def create_response(self, **kwargs) -> None:
            self.responses.append(kwargs)

    records = []
    calls = []
    conversation = FakeConversation()
    provider = QwenOmniRealtimeAdapter(RealtimeProviderConfig(provider="qwen", model="fake-omni"))
    provider._conversation = conversation
    provider._output_modalities = ["text", "audio"]
    provider._callbacks = RealtimeProviderCallbacks(
        audio_delta=lambda audio, fmt, metadata: None,
        audio_done=lambda metadata: None,
        provider_event=records.append,
        error=lambda message, record: records.append({"event": "error", "message": message, **record}),
        tool_call_done=lambda record: calls.append(record) or {"tool_call_id": record["tool_call_id"], "name": record["name"], "ok": True},
    )

    provider._handle_provider_event(
        {
            "type": "response.function_call_arguments.done",
            "call_id": "call-001",
            "name": "echo_realtime",
            "arguments": "{}",
        }
    )
    provider._handle_provider_event(
        {
            "type": "response.output_item.done",
            "item": {
                "type": "function_call",
                "call_id": "call-001",
                "name": "echo_realtime",
                "arguments": "{}",
            },
        }
    )

    assert len(calls) == 1
    assert len(conversation.items) == 1
    assert len(conversation.responses) == 0
    provider._handle_provider_event({"type": "response.done", "status": "completed"})
    assert len(conversation.responses) == 1
    assert any(record.get("event") == "omni.tool_call.duplicate_ignored" for record in records)


def test_realtime_tool_bridge_prefers_done_arguments_over_delta_copy() -> None:
    """测试目标：验证工具参数不会被 delta 和 done 重复拼接。

    测试方法：先追加一次 `{}` 参数增量，再用 done 事件提交完整 `{}` 参数。
    预期结果：工具收到空 dict，而不是无法解析的 `_raw_arguments={}{}`。
    """

    gateway = ArgumentCaptureGateway()
    bridge = RealtimeToolBridge(tool_gateway=gateway)

    bridge.append_tool_call_delta(tool_call_id="call-001", name="echo_realtime", arguments_delta="{}")
    bridge.commit_tool_call(
        tool_call_id="call-001",
        user_id="user-001",
        session_id="sess-001",
        name="echo_realtime",
        arguments="{}",
    )

    assert gateway.input_data == {}


def test_qwen_omni_does_not_suppress_audio_while_generating_tool_arguments() -> None:
    """测试目标：验证 Omni adapter 不再用一刀切方式屏蔽工具期音频。

    测试方法：模拟 provider 进入 function call 参数增量后继续发送 audio delta。
    预期结果：adapter 仍按 provider 原始事件下发 audio delta；是否生成工具前提示
    由 Realtime 提示词约束控制，不在 adapter 层做整段音频吞弃。
    """

    from realtime_agent.agent_core.omni import QwenOmniRealtimeAdapter

    records = []
    audio_chunks = []
    audio_done = []
    provider = QwenOmniRealtimeAdapter(RealtimeProviderConfig(provider="qwen", model="fake-omni"))
    provider._callbacks = RealtimeProviderCallbacks(
        audio_delta=lambda audio, fmt, metadata: audio_chunks.append(audio),
        audio_done=lambda metadata: audio_done.append(metadata),
        provider_event=records.append,
        error=lambda message, record: records.append({"event": "error", "message": message, **record}),
    )

    provider._handle_provider_event({"type": "response.created"})
    provider._handle_provider_event(
        {
            "type": "response.function_call_arguments.delta",
            "call_id": "call-001",
            "name": "capture_photo",
            "delta": "{\"timeout_seconds\":",
        }
    )
    provider._handle_provider_event({"type": "response.audio.delta", "delta": "AQI="})
    provider._handle_provider_event({"type": "response.audio.done"})

    assert audio_chunks == [b"\x01\x02"]
    assert len(audio_done) == 1
    assert any(record.get("event") == "omni.response.audio.delta.decoded" for record in records)
    assert not any(record.get("event") == "omni.response.audio.delta.suppressed" for record in records)


def test_realtime_provider_text_is_persisted_to_user_messages(tmp_path) -> None:
    """测试目标：验证 Omni Realtime 的文本转写会写入用户级 messages。

    测试方法：直接向 OmniRealtimeAgentCore 注入用户输入转写、assistant 文本 delta
    和 assistant done 事件。
    预期结果：`runs/users/<user_id>/messages.jsonl` 同时包含 user 和 assistant 文本。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    core = OmniRealtimeAgentCore(
        control_service=app.control_service,
        output_service=app.output_service,
        recorder=app.recorder,
        omni_config=RealtimeProviderConfig(provider="fake", model="fake-omni"),
        provider_factory=lambda config: FakeRealtimeProvider(config),
        tool_gateway=app.tool_gateway,
    )

    core._record_provider_event(
        user_id="user-001",
        session_id="sess-001",
        record={
            "event": "omni.conversation.item.input_audio_transcription.completed",
            "transcript": "帮我查一下有哪些设备在线。",
        },
    )
    core._record_provider_event(
        user_id="user-001",
        session_id="sess-001",
        record={"event": "omni.response.audio_transcript.delta", "delta": "当前有"},
    )
    core._record_provider_event(
        user_id="user-001",
        session_id="sess-001",
        record={"event": "omni.response.audio_transcript.delta", "delta": " 1 台设备在线。"},
    )
    core._record_provider_event(
        user_id="user-001",
        session_id="sess-001",
        record={"event": "omni.response.audio_transcript.done", "transcript": ""},
    )

    messages = (tmp_path / "runs" / "user-001" / "sess-001" / "messages.jsonl").read_text(encoding="utf-8")
    assert '"role": "user"' in messages
    assert "帮我查一下有哪些设备在线。" in messages
    assert '"role": "assistant"' in messages
    assert "当前有 1 台设备在线。" in messages


def test_realtime_interrupted_provider_text_is_persisted_with_interrupt_marker(tmp_path) -> None:
    """测试目标：验证被用户打断的 Omni partial response 会带打断标记写入 messages。

    测试方法：模拟 provider 开始一轮 response、输出部分文本后收到 speech_started，
    再让旧 response 返回 transcript done；随后再模拟一轮新的正常 response。
    预期结果：旧 partial 只以 `<用户打断>` 结尾写入一次，旧 done 不再追加，
    新的回复正常写入 messages。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    core = OmniRealtimeAgentCore(
        control_service=app.control_service,
        output_service=app.output_service,
        recorder=app.recorder,
        omni_config=RealtimeProviderConfig(provider="fake", model="fake-omni"),
        provider_factory=lambda config: FakeRealtimeProvider(config),
        tool_gateway=app.tool_gateway,
    )

    core._record_provider_event(
        user_id="user-001",
        session_id="sess-001",
        record={
            "event": "omni.conversation.item.input_audio_transcription.completed",
            "transcript": "第一个问题。",
        },
    )
    core._record_provider_event(
        user_id="user-001",
        session_id="sess-001",
        record={"event": "omni.response.created", "provider": "fake"},
    )
    core._record_provider_event(
        user_id="user-001",
        session_id="sess-001",
        record={"event": "omni.response.audio_transcript.delta", "delta": "这段旧回复"},
    )
    core._record_provider_event(
        user_id="user-001",
        session_id="sess-001",
        record={"event": "omni.input_audio_buffer.speech_started", "provider": "fake"},
    )
    core._record_provider_event(
        user_id="user-001",
        session_id="sess-001",
        record={"event": "omni.response.audio_transcript.done", "transcript": "这段旧回复不应写入。"},
    )
    core._record_provider_event(
        user_id="user-001",
        session_id="sess-001",
        record={"event": "omni.response.created", "provider": "fake"},
    )
    core._record_provider_event(
        user_id="user-001",
        session_id="sess-001",
        record={"event": "omni.response.audio_transcript.delta", "delta": "这是新回复。"},
    )
    core._record_provider_event(
        user_id="user-001",
        session_id="sess-001",
        record={"event": "omni.response.audio_transcript.done", "transcript": ""},
    )

    messages = (tmp_path / "runs" / "user-001" / "sess-001" / "messages.jsonl").read_text(encoding="utf-8")
    assert "第一个问题。" in messages
    assert "这段旧回复<用户打断>" in messages
    assert "这段旧回复不应写入" not in messages
    assert "这是新回复。" in messages


def test_realtime_interrupt_keeps_generated_unheard_suffix_in_message(tmp_path) -> None:
    """测试目标：验证 Omni 打断消息保留已生成但未播放的 transcript 后缀。

    测试方法：模拟 provider 已生成“我是乐鑫”，并让 OutputService 估算用户只听到
    “我是”。
    预期结果：Omni messages 中 `<用户打断>` 插入在已播放和未播放文本之间。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    core = OmniRealtimeAgentCore(
        control_service=app.control_service,
        output_service=app.output_service,
        recorder=app.recorder,
        omni_config=RealtimeProviderConfig(provider="fake", model="fake-omni"),
        provider_factory=lambda config: FakeRealtimeProvider(config),
        tool_gateway=app.tool_gateway,
    )

    def estimate_played_text_prefix(*, user_id: str, session_id: str) -> str:
        """模拟 Omni 播放进度：用户只听到了前两个字。"""

        return "我是"

    app.output_service.estimate_played_text_prefix = estimate_played_text_prefix
    core._record_provider_event(
        user_id="user-001",
        session_id="sess-001",
        record={"event": "omni.response.created", "provider": "fake", "response_id": "resp-1"},
    )
    core._record_provider_event(
        user_id="user-001",
        session_id="sess-001",
        record={"event": "omni.response.audio_transcript.delta", "delta": "我是乐鑫", "response_id": "resp-1"},
    )
    core._mark_current_response_interrupted(user_id="user-001", session_id="sess-001", reason="unit_interrupt")

    messages = (tmp_path / "runs" / "user-001" / "sess-001" / "messages.jsonl").read_text(encoding="utf-8")
    assert "我是<用户打断>乐鑫" in messages
    agent_events = app.recorder.session_file("sess-001", "agent-events.jsonl").read_text(encoding="utf-8")
    assert '"split_source": "output_service_estimate"' in agent_events
    assert '"unheard_chars": 2' in agent_events


def test_realtime_stale_provider_response_id_is_ignored_after_new_response(tmp_path) -> None:
    """测试目标：验证 Omni 旧 response 的迟到文本不会跨 generation 写入 messages。

    测试方法：模拟 old response 被打断后创建 new response，再让 old response 带旧
    response_id 返回 done。
    预期结果：旧 done 被忽略，新 response 正常写入。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    core = OmniRealtimeAgentCore(
        control_service=app.control_service,
        output_service=app.output_service,
        recorder=app.recorder,
        omni_config=RealtimeProviderConfig(provider="fake", model="fake-omni"),
        provider_factory=lambda config: FakeRealtimeProvider(config),
        tool_gateway=app.tool_gateway,
    )

    core._record_provider_event(
        user_id="user-001",
        session_id="sess-001",
        record={"event": "omni.response.created", "provider": "fake", "response_id": "resp-old"},
    )
    core._record_provider_event(
        user_id="user-001",
        session_id="sess-001",
        record={"event": "omni.response.audio_transcript.delta", "delta": "旧回复", "response_id": "resp-old"},
    )
    core._mark_current_response_interrupted(user_id="user-001", session_id="sess-001", reason="unit_interrupt")
    core._record_provider_event(
        user_id="user-001",
        session_id="sess-001",
        record={"event": "omni.response.created", "provider": "fake", "response_id": "resp-new"},
    )
    core._record_provider_event(
        user_id="user-001",
        session_id="sess-001",
        record={"event": "omni.response.audio_transcript.done", "transcript": "旧回复不应写入", "response_id": "resp-old"},
    )
    core._record_provider_event(
        user_id="user-001",
        session_id="sess-001",
        record={"event": "omni.response.audio_transcript.delta", "delta": "新回复", "response_id": "resp-new"},
    )
    core._record_provider_event(
        user_id="user-001",
        session_id="sess-001",
        record={"event": "omni.response.audio_transcript.done", "transcript": "", "response_id": "resp-new"},
    )

    messages = (tmp_path / "runs" / "user-001" / "sess-001" / "messages.jsonl").read_text(encoding="utf-8")
    assert "旧回复<用户打断>" in messages
    assert "旧回复不应写入" not in messages
    assert "新回复" in messages


def test_realtime_tool_call_is_persisted_to_user_messages(tmp_path) -> None:
    """测试目标：验证 Omni Realtime 工具调用和结果会写入用户级 messages。

    测试方法：注册测试工具后，直接提交 provider tool done 记录。
    预期结果：messages 中包含 assistant tool_call 和 tool result 两类消息。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    app.tool_registry.register(EchoRealtimeTool())
    core = OmniRealtimeAgentCore(
        control_service=app.control_service,
        output_service=app.output_service,
        recorder=app.recorder,
        omni_config=RealtimeProviderConfig(provider="fake", model="fake-omni"),
        provider_factory=lambda config: FakeRealtimeProvider(config),
        tool_gateway=app.tool_gateway,
    )

    result = core._handle_provider_tool_call_done(
        user_id="user-001",
        session_id="sess-001",
        record={"tool_call_id": "call-001", "name": "echo_realtime", "arguments": '{"value":"ok"}'},
    )

    assert result["ok"] is True
    messages = (tmp_path / "runs" / "user-001" / "sess-001" / "messages.jsonl").read_text(encoding="utf-8")
    assert "assistant_tool_call.done" in messages
    assert "tool_result.done" in messages
    assert "echo_realtime" in messages
    assert "call-001" in messages


def test_realtime_core_replays_last_user_audio_for_capture_photo(tmp_path) -> None:
    """测试目标：验证 capture_photo 后重放的是上一轮用户原始音频。

    测试方法：向 OmniRealtimeAgentCore 写入两片音频和 final 边界，再请求
    capture_photo 工具结果的 replay audio。
    预期结果：返回上一轮非空 PCM 片段，不包含 final 空 chunk。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    core = OmniRealtimeAgentCore(
        control_service=app.control_service,
        output_service=app.output_service,
        recorder=app.recorder,
        omni_config=RealtimeProviderConfig(provider="fake", model="fake-omni"),
        provider_factory=lambda config: FakeRealtimeProvider(config),
        tool_gateway=app.tool_gateway,
    )

    for seq, payload, final in [(0, b"\x01\x02", False), (1, b"\x03\x04", False), (2, b"", True)]:
        core.append_audio_event(
            StreamChunk(
                user_id="user-001",
                session_id="sess-replay",
                stream_id="stream-in",
                stream_type="sensor.mic",
                seq=seq,
                payload=payload,
                final=final,
            )
        )

    chunks = core._replay_audio_for_tool_result(
        session_id="sess-replay",
        result={"name": "capture_photo", "ok": True, "tool_call_id": "call-photo"},
    )

    assert chunks == [b"\x01\x02", b"\x03\x04"]
    model_events = (tmp_path / "runs" / "user-001" / "sess-replay" / "model-events.jsonl").read_text(
        encoding="utf-8"
    )
    assert "omni.input_audio.replay.prepared" in model_events


def test_mock_omni_provider_cancel_and_close_are_observable() -> None:
    """测试目标：验证内置 mock omni provider 可被测试直接观测。

    测试方法：直接实例化 provider，调用 open、cancel、close。
    预期结果：provider 不访问网络，并记录 cancelled / closed 状态。
    """

    provider = MockRealtimeProviderAdapter(RealtimeProviderConfig(provider="mock", model="mock-omni"))
    records = []
    provider.open(
        user_id="user-001",
        session_id="sess-001",
        callbacks=RealtimeProviderCallbacks(
            audio_delta=lambda audio, fmt, metadata: None,
            audio_done=lambda metadata: None,
            provider_event=records.append,
            error=lambda message, record: None,
        ),
    )

    provider.cancel(user_id="user-001", reason="unit")
    provider.close(user_id="user-001", reason="unit")

    assert provider.cancelled is True
    assert provider.closed is True
    assert records[0]["event"] == "mock_omni.session.opened"


def _new_failing_fake(config: RealtimeProviderConfig, instances: list[FailingRealtimeProvider]) -> FailingRealtimeProvider:
    fake = FailingRealtimeProvider(config)
    instances.append(fake)
    return fake
