from __future__ import annotations

from audio_chat.agent_core.realtime import (
    MockRealtimeProviderAdapter,
    RealtimeAudioAgentCore,
    RealtimeProviderCallbacks,
    RealtimeProviderConfig,
)
from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.protocol import Event, StreamChunk, StreamFormat


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
                "capabilities": {
                    "streams.produce": ["sensor.mic"],
                    "streams.consume": ["actuator.speaker"],
                },
                "subscriptions": [
                    {"event": "control.audio_session.*"},
                    {"event": "stream.output.*", "filter": {"stream_type": "actuator.speaker"}},
                ],
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
    model_events = (tmp_path / "runs" / "sessions" / handle.session_id / "model-events.jsonl").read_text()
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
    model_events = (tmp_path / "runs" / "sessions" / handle.session_id / "model-events.jsonl").read_text()
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
