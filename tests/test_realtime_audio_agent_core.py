from __future__ import annotations

from audio_chat.agent_core.realtime import (
    MockRealtimeProviderAdapter,
    RealtimeAudioAgentCore,
    RealtimeProviderCallbacks,
    RealtimeProviderConfig,
    RealtimeToolBridge,
)
from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.protocol import Event, StreamChunk, StreamFormat
from audio_chat.tools import BaseTool, ToolContext, ToolResult


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


def register_speaker(app: AudioChatApp, connection: Connection, user_id: str = "user-001") -> None:
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
                "properties": {"audio_chat.audio_output": "actuator.speaker"},
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

    def cancel(self, *, user_id: str, reason: str) -> None:
        """记录 cancel 调用。"""
        self.cancelled = True

    def close(self, *, user_id: str, reason: str) -> None:
        """记录 close 调用。"""
        self.closed = True


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


def _realtime_app(tmp_path, instances: list[FakeRealtimeProvider]) -> AudioChatApp:
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), agent_mode="text"))
    app.agent_core = RealtimeAudioAgentCore(
        output_service=app.output_service,
        recorder=app.recorder,
        realtime_config=RealtimeProviderConfig(provider="fake", model="fake-omni"),
        provider_factory=lambda config: _new_fake(config, instances),
    )
    app.text_agent_core = app.agent_core
    app.audio_pipeline.agent_core = app.agent_core
    return app


def _new_fake(config: RealtimeProviderConfig, instances: list[FakeRealtimeProvider]) -> FakeRealtimeProvider:
    fake = FakeRealtimeProvider(config)
    instances.append(fake)
    return fake


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


def test_realtime_audio_done_closes_current_output_stream(tmp_path) -> None:
    """测试目标：验证 provider audio done 会关闭当前 output stream。

    测试方法：先 append 一片音频触发 output stream，再让 fake provider 发送 done。
    预期结果：端侧收到 `stream.output.close.requested`。
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

    assert any(event.event_name == "stream.output.close.requested" for event in connection.events)
    model_events = (tmp_path / "runs" / "user-001" / handle.session_id / "model-events.jsonl").read_text()
    assert "assistant_audio.done" in model_events


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


def test_realtime_provider_failure_suppresses_repeated_append_errors(tmp_path) -> None:
    """测试目标：验证 provider 失败后不再对同 session 继续 append。

    测试方法：注入会抛 `Connection is already closed.` 的 fake provider，连续写入两片
    mic chunk。
    预期结果：provider append 只调用一次，runs 只记录一次 `realtime.session.failed`。
    """
    instances: list[FailingRealtimeProvider] = []
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), agent_mode="text"))
    app.agent_core = RealtimeAudioAgentCore(
        output_service=app.output_service,
        recorder=app.recorder,
        realtime_config=RealtimeProviderConfig(provider="fake", model="fake-omni"),
        provider_factory=lambda config: _new_failing_fake(config, instances),
    )
    app.text_agent_core = app.agent_core
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
    assert model_events.count("realtime.session.failed") == 1


def test_realtime_mode_uses_builtin_mock_provider_for_local_chain(tmp_path) -> None:
    """测试目标：验证 realtime_audio 模式具备稳定 mock provider 链路。

    测试方法：配置 `realtime_provider=mock` 创建 app，发送一片 final mic chunk。
    预期结果：端侧收到 speaker chunk 和 close 事件，core events 记录 session 与响应事件。
    """

    app = AudioChatApp(
        AudioChatConfig(
            runs_root=str(tmp_path / "runs"),
            agent_mode="realtime_audio",
            realtime_provider="mock",
            realtime_model="mock-realtime",
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
    assert any(event.event_name == "stream.output.close.requested" for event in connection.events)
    assert any(event.event == "session.opened" for event in app.agent_core.events())
    assert any(event.event == "mock_realtime.input.committed" for event in app.agent_core.events())


def test_realtime_commit_input_forwards_to_provider_and_records_event(tmp_path) -> None:
    """测试目标：验证 RealtimeAudioAgentCore 的公共 `commit_input` 接口。

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

    assert any(event.event_name == "stream.output.close.requested" for event in connection.events)
    assert any(event.event == "input.committed" for event in app.agent_core.events())


def test_realtime_open_records_equivalent_model_request_and_injects_tool_schema(tmp_path) -> None:
    """测试目标：验证 Omni Realtime 也有等价 model request 和工具 schema。

    测试方法：注册一个测试 Tool，注入 fake realtime provider，写入一片 mic chunk 打开会话。
    预期结果：provider config 收到扁平 function schema，runs 中落盘 messages/tools 快照。
    """

    instances: list[FakeRealtimeProvider] = []
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), agent_mode="text"))
    app.tool_registry.register(EchoRealtimeTool())
    app.agent_core = RealtimeAudioAgentCore(
        output_service=app.output_service,
        recorder=app.recorder,
        realtime_config=RealtimeProviderConfig(
            provider="fake",
            model="fake-omni",
            instructions="你是测试用 Omni 助手。",
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
    model_request = (tmp_path / "runs" / "user-001" / handle.session_id / "model-request.json").read_text(
        encoding="utf-8"
    )
    assert "agent_core_realtime_audio" in model_request
    assert "input_audio_stream" in model_request
    assert "你是测试用 Omni 助手。" in model_request
    assert "echo_realtime" in model_request


def test_qwen_omni_tool_result_is_injected_back_to_conversation() -> None:
    """测试目标：验证 Qwen Omni 工具结果会回填到同一条 Realtime 会话。

    测试方法：绕过网络注入 fake conversation，直接触发 provider tool done 事件。
    预期结果：conversation 收到 `function_call_output`，并在原 response.done 后继续创建音频响应。
    """

    from audio_chat.agent_core.realtime import QwenOmniRealtimeAdapter

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
    provider = QwenOmniRealtimeAdapter(RealtimeProviderConfig(provider="qwen", model="fake-omni", instructions="继续回答"))
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


def test_qwen_omni_tool_failure_followup_instructions_force_failure_ack() -> None:
    """测试目标：验证 Realtime 工具失败后 follow-up 明确约束模型承认失败。

    测试方法：模拟 task_runtime_manager 启动任务失败，触发工具结果回填和 response.done。
    预期结果：创建 follow-up response 的 instructions 包含失败原因，并禁止声称成功。
    """

    from audio_chat.agent_core.realtime import QwenOmniRealtimeAdapter

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
    provider = QwenOmniRealtimeAdapter(RealtimeProviderConfig(provider="qwen", model="fake-omni", instructions="基础指令"))
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
    assert "工具调用失败" in instructions
    assert "unknown task: timer" in instructions
    assert "任务没有启动" in instructions
    assert "不能声称工具已经执行成功" in instructions


def test_qwen_omni_final_audio_chunk_commits_input_boundary() -> None:
    """测试目标：验证 Qwen Omni 收到 endpoint final chunk 时会提交输入边界。

    测试方法：给 `QwenOmniRealtimeAdapter` 注入 fake conversation，发送一片
    `final=true` 的 sensor.mic chunk。
    预期结果：conversation 收到音频 append，并调用 DashScope SDK 的 `commit()`。
    """

    from audio_chat.agent_core.realtime import QwenOmniRealtimeAdapter

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
    预期结果：conversation 先收到 base64 图片，再收到 `function_call_output`，最后
    创建下一段文本和音频响应，保证工具完成前图片已经进入 Realtime 上文。
    """

    from audio_chat.agent_core.realtime import QwenOmniRealtimeAdapter

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
    provider = QwenOmniRealtimeAdapter(RealtimeProviderConfig(provider="qwen", model="fake-omni", instructions="结合图片回答"))
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

    from audio_chat.agent_core.realtime import QwenOmniRealtimeAdapter

    image_path = tmp_path / "photo-uri.jpg"
    image_path.write_bytes(b"\xff\xd8browser-photo-uri\xff\xd9")

    class FakeConversation:
        """记录 provider adapter 写回的会话操作。"""

        def __init__(self) -> None:
            self.items = []
            self.audios = []
            self.videos = []
            self.commits = 0

        def create_item(self, item: dict) -> None:
            self.items.append(item)

        def append_video(self, image_base64: str) -> None:
            self.videos.append(image_base64)

        def append_audio(self, audio_base64: str) -> None:
            self.audios.append(audio_base64)

        def commit(self) -> None:
            self.commits += 1

    records = []
    conversation = FakeConversation()
    provider = QwenOmniRealtimeAdapter(RealtimeProviderConfig(provider="qwen", model="fake-omni", instructions="结合图片回答"))
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
    assert not any(record.get("event") == "omni.capture_photo.image_append.missing_image" for record in records)


def test_qwen_omni_duplicate_tool_done_is_ignored() -> None:
    """测试目标：验证 Qwen Omni 同一个工具调用只执行并回填一次。

    测试方法：模拟 provider 先发送 `function_call_arguments.done`，随后又发送
    同 call_id 的 `output_item.done`。
    预期结果：工具回调和 conversation item 只发生一次，follow-up response 只创建一次，并记录重复忽略事件。
    """

    from audio_chat.agent_core.realtime import QwenOmniRealtimeAdapter

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


def test_realtime_provider_text_is_persisted_to_user_messages(tmp_path) -> None:
    """测试目标：验证 Omni Realtime 的文本转写会写入用户级 messages。

    测试方法：直接向 RealtimeAudioAgentCore 注入用户输入转写、assistant 文本 delta
    和 assistant done 事件。
    预期结果：`runs/users/<user_id>/messages.jsonl` 同时包含 user 和 assistant 文本。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), agent_mode="text"))
    core = RealtimeAudioAgentCore(
        control_service=app.control_service,
        output_service=app.output_service,
        recorder=app.recorder,
        realtime_config=RealtimeProviderConfig(provider="fake", model="fake-omni"),
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


def test_realtime_tool_call_is_persisted_to_user_messages(tmp_path) -> None:
    """测试目标：验证 Omni Realtime 工具调用和结果会写入用户级 messages。

    测试方法：注册测试工具后，直接提交 provider tool done 记录。
    预期结果：messages 中包含 assistant tool_call 和 tool result 两类消息。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), agent_mode="text"))
    app.tool_registry.register(EchoRealtimeTool())
    core = RealtimeAudioAgentCore(
        control_service=app.control_service,
        output_service=app.output_service,
        recorder=app.recorder,
        realtime_config=RealtimeProviderConfig(provider="fake", model="fake-omni"),
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

    测试方法：向 RealtimeAudioAgentCore 写入两片音频和 final 边界，再请求
    capture_photo 工具结果的 replay audio。
    预期结果：返回上一轮非空 PCM 片段，不包含 final 空 chunk。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), agent_mode="text"))
    core = RealtimeAudioAgentCore(
        control_service=app.control_service,
        output_service=app.output_service,
        recorder=app.recorder,
        realtime_config=RealtimeProviderConfig(provider="fake", model="fake-omni"),
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
    assert "realtime.input_audio.replay.prepared" in model_events


def test_mock_realtime_provider_cancel_and_close_are_observable() -> None:
    """测试目标：验证内置 mock realtime provider 可被测试直接观测。

    测试方法：直接实例化 provider，调用 open、cancel、close。
    预期结果：provider 不访问网络，并记录 cancelled / closed 状态。
    """

    provider = MockRealtimeProviderAdapter(RealtimeProviderConfig(provider="mock", model="mock-realtime"))
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
    assert records[0]["event"] == "mock_realtime.session.opened"


def _new_failing_fake(config: RealtimeProviderConfig, instances: list[FailingRealtimeProvider]) -> FailingRealtimeProvider:
    fake = FailingRealtimeProvider(config)
    instances.append(fake)
    return fake
