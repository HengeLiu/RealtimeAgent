from pathlib import Path

from audio_chat.agent_core import TextAgentCore
from audio_chat.audio_pipeline import AudioPipeline, FormatNormalizer
from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.protocol import Event, StreamChunk, StreamChunkCodec, StreamFormat


ROOT = Path(__file__).resolve().parents[1]


def test_stream_chunk_codec_matches_golden_binary() -> None:
    """测试目标：冻结 StreamChunk 二进制契约。

    测试方法：构造固定 timestamp、seq、metadata 和 payload 的 chunk，与 golden bin
    完全比对，再反解检查 header 长度、payload_size、final 和 metadata。
    预期结果：编码结果可跨端复用，解码后字段不丢失。
    """

    chunk = StreamChunk(
        user_id="user-golden",
        session_id="sess-golden",
        stream_id="stream-golden",
        stream_type="sensor.mic",
        seq=7,
        payload=b"\x01\x02\x03\x04",
        timestamp_ms=1760000000123,
        final=True,
        metadata={"trace_id": "golden-stream"},
    )
    encoded = StreamChunkCodec.encode(chunk)
    golden = (ROOT / "testdata/contracts/streams/stream_chunk_pcm16le.bin").read_bytes()

    assert encoded == golden
    header_len = int.from_bytes(encoded[:4], "big")
    assert header_len == len(encoded) - 4 - len(chunk.payload)
    decoded = StreamChunkCodec.decode(encoded)
    assert decoded.seq == 7
    assert decoded.timestamp_ms == 1760000000123
    assert decoded.final is True
    assert decoded.metadata == {"trace_id": "golden-stream"}
    assert decoded.payload == b"\x01\x02\x03\x04"


def test_audio_pipeline_rejects_non_mic_stream() -> None:
    app = AudioChatApp(AudioChatConfig(runs_root="runs/test-audio-pipeline"))
    pipeline = AudioPipeline(text_agent_core=app.text_agent_core)
    chunk = StreamChunk(
        user_id="user-001",
        session_id="sess-001",
        stream_id="stream-001",
        stream_type="sensor.rgb",
        seq=0,
        payload=b"not-audio",
    )

    try:
        pipeline.process(chunk)
    except ValueError as exc:
        assert "sensor.mic" in str(exc)
    else:
        raise AssertionError("Audio Pipeline accepted non sensor.mic stream")


def test_format_normalizer_accepts_default_sensor_mic_format() -> None:
    normalizer = FormatNormalizer()
    chunk = StreamChunk(
        user_id="user-001",
        session_id="sess-001",
        stream_id="stream-001",
        stream_type="sensor.mic",
        seq=0,
        payload=b"\x00\x00",
    )

    assert normalizer.process(chunk) == chunk


def test_default_stream_limit_accepts_browser_jpeg_asset(tmp_path) -> None:
    """测试目标：验证默认 stream 限制可以承载浏览器抓拍 JPEG。

    测试方法：使用默认 `AudioChatConfig` 打开 `sensor.rgb` 输入流，并上传一个明显
    大于 8KiB 的 JPEG payload。
    预期结果：服务端不再按音频小包限制拒绝图片，资产能进入缓存。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    handle = app.open_input_stream(
        user_id="user-browser-photo",
        producer_id="dev-browser-glass",
        stream_type="sensor.rgb",
        format=StreamFormat(codec="jpeg", sample_rate=1, channels=1, chunk_ms=1),
    )
    payload = b"\xff\xd8" + (b"browser-photo" * 2048) + b"\xff\xd9"

    app.write_input_chunk(
        StreamChunk(
            user_id="user-browser-photo",
            session_id=handle.session_id,
            stream_id=handle.stream_id,
            stream_type="sensor.rgb",
            seq=0,
            payload=payload,
            codec="jpeg",
            sample_rate=1,
            channels=1,
            duration_ms=1,
            final=True,
            metadata={"request_id": "asset_req_browser"},
        )
    )

    asset = app.asset_service.query_assets(user_id="user-browser-photo", stream_type="sensor.rgb")[-1]
    assert asset.metadata["payload_size"] == len(payload)
    assert Path(asset.uri).is_absolute()
    assert Path(asset.uri).read_bytes() == payload


def test_device_registration_reports_effective_stream_limit(tmp_path) -> None:
    """测试目标：验证设备注册回执展示真实生效的 stream 单包限制。

    测试方法：用自定义 `stream_max_chunk_bytes` 创建 app，再注册设备。
    预期结果：`control.device.registered` 中的 `effective_config` 与 app 配置一致，
    返回当前配置的 chunk 大小。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), stream_max_chunk_bytes=123456))
    response = app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id="user-effective-config",
            producer_id="dev-effective-config",
            payload={
                "device_id": "dev-effective-config",
                "auth": {"mode": "disabled"},
                "supports": {"sensors": [{"type": "rgb"}], "actuators": []},
            },
        )
    )

    assert response.payload["effective_config"]["stream.max_chunk_bytes"] == 123456


def test_text_agent_core_final_mic_chunk_emits_output() -> None:
    app = AudioChatApp(AudioChatConfig(runs_root="runs/test-agent-core"))

    class Connection:
        device_id = "dev-playback"

        def __init__(self) -> None:
            self.events = []
            self.chunks = []

        def push_event(self, event: Event) -> None:
            self.events.append(event)

        def push_stream_chunk(self, chunk: StreamChunk) -> None:
            self.chunks.append(chunk)

    connection = Connection()
    app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id="user-001",
            producer_id="dev-playback",
            payload={
                "device_id": "dev-playback",
                "auth": {"mode": "disabled"},
                "supports": {"sensors": [], "actuators": []},
            },
        ),
        connection,
    )
    handle = app.open_input_stream(user_id="user-001", producer_id="dev-playback")

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

    assert any(event.event_name == "stream.output.open.requested" for event in connection.events)
    assert connection.chunks


def test_text_agent_core_replies_to_multiple_input_streams_in_same_session(tmp_path) -> None:
    """测试目标：验证连续对话同一 session 内的多轮麦克风输入都能触发回复。

    测试方法：注册一个浏览器式端侧，在同一 active session 下依次打开两条
    `sensor.mic` stream，并分别发送 final chunk。
    预期结果：两条不同输入 stream 都会触发 `agent.response.started` 和音频输出，
    不会因为 session_id 已响应过而跳过第二轮。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))

    class Connection:
        device_id = "dev-continuous"

        def __init__(self) -> None:
            self.events = []
            self.chunks = []

        def push_event(self, event: Event) -> None:
            self.events.append(event)

        def push_stream_chunk(self, chunk: StreamChunk) -> None:
            self.chunks.append(chunk)

    connection = Connection()
    app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id="user-continuous",
            producer_id="dev-continuous",
            payload={
                "device_id": "dev-continuous",
                "auth": {"mode": "disabled"},
                "supports": {"sensors": [], "actuators": []},
            },
        ),
        connection,
    )
    first = app.open_input_stream(user_id="user-continuous", producer_id="dev-continuous")
    second = app.open_input_stream(user_id="user-continuous", producer_id="dev-continuous")
    assert first.session_id == second.session_id

    for index, handle in enumerate((first, second)):
        app.write_input_chunk(
            StreamChunk(
                user_id="user-continuous",
                session_id=handle.session_id,
                stream_id=handle.stream_id,
                stream_type="sensor.mic",
                seq=index,
                payload=b"\x00\x00" * 320,
                final=True,
            )
        )

    event_names = [event.event_name for event in connection.events]
    assert event_names.count("agent.response.started") == 2
    assert event_names.count("stream.output.open.requested") == 2
    assert len(connection.chunks) >= 2


def test_stream_lifecycle_idle_timeout_and_input_failed_events(tmp_path) -> None:
    """测试目标：覆盖输入 stream 的 failed、idle timeout 生命周期。

    测试方法：打开 sensor.mic stream 后分别触发 `fail_stream()` 和
    `close_idle_streams()`。
    预期结果：运行态进入 failed/closed，并写入对应生命周期事件。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), stream_idle_timeout_seconds=0.01))
    failed = app.open_input_stream(user_id="user-life", producer_id="dev-life")

    app.stream_service.fail_stream(failed.stream_id, reason="bad_header")

    assert app.stream_service.registry.get(failed.stream_id).state == "failed"

    idle = app.open_input_stream(user_id="user-life", producer_id="dev-life-2")
    idle.last_activity_at -= 10
    closed = app.stream_service.close_idle_streams(now=idle.last_activity_at + 10)

    assert [handle.stream_id for handle in closed] == [idle.stream_id]
    assert app.stream_service.registry.get(idle.stream_id).state == "closed"


def test_output_stream_freezes_consumers_for_chunks_close_and_cancel(tmp_path) -> None:
    """测试目标：确认 output stream 打开时冻结消费者。

    测试方法：先注册第一个 speaker 设备并打开 output stream，再注册第二个设备；
    随后写 chunk、close 和 cancel。
    预期结果：后续字节和生命周期事件只发送给打开 stream 时匹配到的第一个设备。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))

    class Connection:
        def __init__(self, device_id: str) -> None:
            self.device_id = device_id
            self.events = []
            self.chunks = []

        def push_event(self, event: Event) -> None:
            self.events.append(event)

        def push_stream_chunk(self, chunk: StreamChunk) -> None:
            self.chunks.append(chunk)

        def close(self, *, reason: str) -> None:
            self.closed_reason = reason

    def register(connection: Connection) -> None:
        app.register_device(
            Event(
                event_name="control.device.register.requested",
                user_id="user-output",
                producer_id=connection.device_id,
                payload={
                    "device_id": connection.device_id,
                    "auth": {"mode": "disabled"},
                    "supports": {"sensors": [], "actuators": []},
                },
            ),
            connection,
        )

    first = Connection("dev-first-speaker")
    second = Connection("dev-second-speaker")
    register(first)
    handle = app.stream_service.open_stream(
        user_id="user-output",
        session_id="sess-output",
        stream_type="actuator.speaker",
        producer_id="server-main",
        format=StreamFormat(chunk_ms=40),
    )
    cancel_handle = app.stream_service.open_stream(
        user_id="user-output",
        session_id="sess-output",
        stream_type="actuator.speaker",
        producer_id="server-main",
        format=StreamFormat(chunk_ms=40),
    )
    register(second)

    app.stream_service.write_chunk(
        StreamChunk(
            user_id="user-output",
            session_id=handle.session_id,
            stream_id=handle.stream_id,
            stream_type="actuator.speaker",
            seq=0,
            payload=b"\x00\x01",
            duration_ms=40,
        )
    )
    app.stream_service.close_stream(handle.stream_id, reason="done")
    app.stream_service.cancel_stream(cancel_handle.stream_id, reason="barge_in")

    assert handle.consumer_device_ids == ("dev-first-speaker",)
    assert cancel_handle.consumer_device_ids == ("dev-first-speaker",)
    assert [chunk.stream_id for chunk in first.chunks] == [handle.stream_id]
    assert second.chunks == []
    assert any(event.event_name == "stream.output.close.requested" for event in first.events)
    assert any(event.event_name == "stream.output.cancelled" for event in first.events)
    assert [event.event_name for event in second.events] == []
