import json
from threading import Event as ThreadEvent
from pathlib import Path

import realtime_agent.agent_core.vision as text_module
from realtime_agent.agent_core import VisionRealtimeAgentCore
from realtime_agent.agent_core.providers import TranscriptEvent
from realtime_agent.audio_pipeline import AudioPipeline, FormatNormalizer
from realtime_agent.app import RealtimeAgentApp, RealtimeAgentConfig
from realtime_agent.protocol import Event, StreamChunk, StreamChunkCodec, StreamFormat


def test_stream_chunk_codec_preserves_binary_header_and_payload() -> None:
    """测试目标：确认 StreamChunk 编解码保持字段和 payload 完整。

    测试方法：构造固定 timestamp、seq、metadata 和 payload 的 chunk，编码后再解码。
    预期结果：header 长度正确，解码后字段不丢失。
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

    header_len = int.from_bytes(encoded[:4], "big")
    assert header_len == len(encoded) - 4 - len(chunk.payload)
    decoded = StreamChunkCodec.decode(encoded)
    assert decoded.seq == 7
    assert decoded.timestamp_ms == 1760000000123
    assert decoded.final is True
    assert decoded.metadata == {"trace_id": "golden-stream"}
    assert decoded.payload == b"\x01\x02\x03\x04"


def test_audio_pipeline_rejects_non_mic_stream() -> None:
    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root="runs/test-audio-pipeline"))
    pipeline = AudioPipeline(vision_agent_core=app.vision_agent_core)
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

    测试方法：使用默认 `RealtimeAgentConfig` 打开 `sensor.rgb` 输入流，并上传一个明显
    大于 8KiB 的 JPEG payload。
    预期结果：服务端不再按音频小包限制拒绝图片，资产能进入缓存。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
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
    assert app.asset_service.wait_for_archive(asset.asset_id, timeout_seconds=1)
    assert Path(asset.uri).read_bytes() == payload


def test_sensor_chunk_before_open_event_auto_registers_input_stream(tmp_path) -> None:
    """测试目标：验证 stream chunk 先于 opened 控制事件到达时不会被误判为 unknown stream。

    测试方法：不预先调用 `open_input_stream()`，直接写入带 request_id 的 `sensor.rgb`
    单帧 chunk，模拟 control WebSocket 和 stream WebSocket 跨连接乱序。
    预期结果：服务端按 chunk 补注册输入 stream，图片资产正常进入 runs 归档。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    payload = b"\xff\xd8" + b"race-photo" + b"\xff\xd9"
    app.write_input_chunk(
        StreamChunk(
            user_id="user-race-photo",
            session_id="dev-ios-001",
            stream_id="stream-rgb-race",
            stream_type="sensor.rgb",
            seq=0,
            payload=payload,
            codec="jpeg",
            sample_rate=1,
            channels=1,
            duration_ms=1,
            final=True,
            metadata={"request_id": "asset_req_race"},
        )
    )

    handle = app.stream_service.registry.get("stream-rgb-race")
    assert handle.producer_id == "dev-ios-001"
    assert handle.consumer_device_ids == ()
    asset = app.asset_service.query_assets(user_id="user-race-photo", stream_type="sensor.rgb")[-1]
    assert asset.metadata["request_id"] == "asset_req_race"
    assert app.asset_service.wait_for_archive(asset.asset_id, timeout_seconds=1)
    assert Path(asset.uri).read_bytes() == payload


def test_rgb_asset_enters_turn_buffer_and_can_be_claimed_once(tmp_path) -> None:
    """测试目标：验证 sensor.rgb 上传后进入 turn buffer，并且只能被业务消费一次。

    测试方法：上传一帧带 turn_id / ttl / direction 的 RGB chunk，随后通过
    `claim_photo_asset()` 连续 claim 两次。
    预期结果：第一次 claim 成功，第二次返回 already_claimed；磁盘资产仍可查询。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    handle = app.open_input_stream(
        user_id="user-photo-buffer",
        producer_id="dev-photo-buffer",
        stream_type="sensor.rgb",
        format=StreamFormat(codec="jpeg", sample_rate=1, channels=1, chunk_ms=1),
    )
    app.write_input_chunk(
        StreamChunk(
            user_id="user-photo-buffer",
            session_id=handle.session_id,
            stream_id=handle.stream_id,
            stream_type="sensor.rgb",
            seq=0,
            payload=b"\xff\xd8photo-buffer\xff\xd9",
            codec="jpeg",
            sample_rate=1,
            channels=1,
            duration_ms=1,
            final=True,
            metadata={
                "request_id": "asset_req_buffer",
                "turn_id": "turn-buffer-1",
                "ttl_seconds": 5,
                "capture_reason": "capture_photo",
                "direction": "front",
            },
        )
    )

    asset = app.asset_service.query_assets(user_id="user-photo-buffer", stream_type="sensor.rgb")[-1]
    first = app.asset_service.claim_photo_asset(
        asset_id=asset.asset_id,
        consumer="agent_inline",
        owner="test",
        reason="unit",
    )
    second = app.asset_service.claim_photo_asset(
        asset_id=asset.asset_id,
        consumer="tool_internal",
        owner="test",
        reason="unit",
    )

    assert asset.metadata["turn_id"] == "turn-buffer-1"
    assert asset.metadata["direction"] == "front"
    assert first.ok is True
    assert second.ok is False
    assert second.reason == "already_claimed"
    assert app.asset_service.query_assets(user_id="user-photo-buffer", stream_type="sensor.rgb")


def test_turn_buffer_clear_does_not_delete_runs_asset(tmp_path) -> None:
    """测试目标：验证 turn 结束只清理内存 buffer，不删除磁盘 runs 资产。

    测试方法：上传一帧 RGB 后调用 `clear_turn_buffer()`，再尝试 claim 和查询磁盘资产。
    预期结果：claim 返回 not_buffered，但 `query_assets()` 仍可读到 AssetRef 和文件。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    handle = app.open_input_stream(
        user_id="user-photo-clear",
        producer_id="dev-photo-clear",
        stream_type="sensor.rgb",
        format=StreamFormat(codec="jpeg", sample_rate=1, channels=1, chunk_ms=1),
    )
    app.write_input_chunk(
        StreamChunk(
            user_id="user-photo-clear",
            session_id=handle.session_id,
            stream_id=handle.stream_id,
            stream_type="sensor.rgb",
            seq=0,
            payload=b"\xff\xd8photo-clear\xff\xd9",
            codec="jpeg",
            sample_rate=1,
            channels=1,
            duration_ms=1,
            final=True,
            metadata={"turn_id": "turn-clear-1", "ttl_seconds": 5},
        )
    )
    asset = app.asset_service.query_assets(user_id="user-photo-clear", stream_type="sensor.rgb")[-1]

    cleared = app.asset_service.clear_turn_buffer(
        user_id="user-photo-clear",
        session_id=handle.session_id,
        turn_id="turn-clear-1",
        reason="unit_turn_end",
    )
    claim = app.asset_service.claim_photo_asset(
        asset_id=asset.asset_id,
        consumer="agent_inline",
        owner="test",
        reason="after_clear",
    )

    assert cleared == 1
    assert claim.ok is False
    assert claim.reason == "not_buffered"
    assert app.asset_service.wait_for_archive(asset.asset_id, timeout_seconds=1)
    assert Path(asset.uri).is_file()


def test_rgb_asset_ttl_expiry_blocks_claim_but_keeps_archive(tmp_path) -> None:
    """测试目标：验证 ttl_seconds 只限制 turn buffer 消费，不影响 runs 归档。

    测试方法：上传一帧 TTL 很短的 RGB 图片，等待过期后 claim。
    预期结果：claim 返回 expired，异步归档文件仍存在。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    handle = app.open_input_stream(
        user_id="user-photo-expire",
        producer_id="dev-photo-expire",
        stream_type="sensor.rgb",
        format=StreamFormat(codec="jpeg", sample_rate=1, channels=1, chunk_ms=1),
    )
    app.write_input_chunk(
        StreamChunk(
            user_id="user-photo-expire",
            session_id=handle.session_id,
            stream_id=handle.stream_id,
            stream_type="sensor.rgb",
            seq=0,
            payload=b"\xff\xd8photo-expire\xff\xd9",
            codec="jpeg",
            sample_rate=1,
            channels=1,
            duration_ms=1,
            final=True,
            metadata={"turn_id": "turn-expire-1", "ttl_seconds": 0.001},
        )
    )
    asset = app.asset_service.query_assets(user_id="user-photo-expire", stream_type="sensor.rgb")[-1]

    import time

    time.sleep(0.01)
    claim = app.asset_service.claim_photo_asset(
        asset_id=asset.asset_id,
        consumer="agent_inline",
        owner="test",
        reason="after_expired",
    )

    assert claim.ok is False
    assert claim.reason == "expired"
    assert app.asset_service.wait_for_archive(asset.asset_id, timeout_seconds=1)
    assert Path(asset.uri).is_file()


def test_rgb_asset_memory_payload_available_before_archive(tmp_path, monkeypatch) -> None:
    """测试目标：验证异步归档未完成时主链路仍可读取内存 payload。

    测试方法：阻塞 AssetStore 后台归档线程，上传 RGB 后立即读取内存 payload。
    预期结果：`write_input_chunk()` 不等待磁盘写入，payload 可从内存读取，归档释放后
    文件最终落盘。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    release_archive = ThreadEvent()
    archive_started = ThreadEvent()
    original_archive = app.asset_service.store._archive_payload

    def delayed_archive(**kwargs):
        archive_started.set()
        release_archive.wait(timeout=1)
        return original_archive(**kwargs)

    monkeypatch.setattr(app.asset_service.store, "_archive_payload", delayed_archive)
    handle = app.open_input_stream(
        user_id="user-photo-memory",
        producer_id="dev-photo-memory",
        stream_type="sensor.rgb",
        format=StreamFormat(codec="jpeg", sample_rate=1, channels=1, chunk_ms=1),
    )
    payload = b"\xff\xd8photo-memory\xff\xd9"

    app.write_input_chunk(
        StreamChunk(
            user_id="user-photo-memory",
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
            metadata={"turn_id": "turn-memory-1", "ttl_seconds": 5},
        )
    )
    asset = app.asset_service.query_assets(user_id="user-photo-memory", stream_type="sensor.rgb")[-1]

    assert archive_started.wait(timeout=1)
    assert app.asset_service.wait_for_archive(asset.asset_id, timeout_seconds=0.01) is False
    assert app.asset_service.get_asset_payload(asset.asset_id) == payload

    release_archive.set()
    assert app.asset_service.wait_for_archive(asset.asset_id, timeout_seconds=1)
    assert Path(asset.uri).read_bytes() == payload


def test_device_registration_reports_effective_stream_limit(tmp_path) -> None:
    """测试目标：验证设备注册回执展示真实生效的 stream 单包限制。

    测试方法：用自定义 `stream_max_chunk_bytes` 创建 app，再注册设备。
    预期结果：`control.device.registered` 中的 `effective_config` 与 app 配置一致，
    返回当前配置的 chunk 大小。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), stream_max_chunk_bytes=123456))
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


def test_vision_agent_core_final_mic_chunk_emits_output() -> None:
    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root="runs/test-agent-core", agent_mode="vision"))

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
                    "properties": {"realtime_agent.audio_output": "actuator.speaker"},
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


def test_vision_agent_core_replies_to_multiple_input_streams_in_same_session(tmp_path) -> None:
    """测试目标：验证连续对话同一 session 内的多轮麦克风输入都能触发回复。

    测试方法：注册一个浏览器式端侧，在同一 active session 下依次打开两条
    `sensor.mic` stream，并分别发送 final chunk。
    预期结果：两条不同输入 stream 都会触发 `agent.response.started` 和音频输出，
    不会因为 session_id 已响应过而跳过第二轮。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))

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
                "supports": {
                    "sensors": [{"type": "mic"}],
                    "actuators": [{"type": "speaker"}],
                },
                "properties": {"realtime_agent.audio_output": "actuator.speaker"},
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

    messages_path = app.recorder.session_file(first.session_id, "messages.jsonl")
    messages = [json.loads(line) for line in messages_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [message["role"] for message in messages].count("user") == 2
    assert [message["role"] for message in messages].count("assistant") == 2


def test_vision_agent_core_recreates_asr_provider_for_each_input_stream(tmp_path, monkeypatch) -> None:
    """测试目标：验证 Vision 链路每条麦克风输入流使用独立 ASR 会话。

    测试方法：把 ASR provider 替换成 final 后关闭自身的假实现，在同一 session 下
    连续提交两条 `sensor.mic` stream。
    预期结果：第二条输入流会创建新的 ASR provider，并正常写入第二轮用户消息，
    不会复用第一轮已关闭的 realtime ASR 会话。
    """

    created_providers = []

    class ClosingAsrProvider:
        provider_name = "closing-asr"

        def __init__(self) -> None:
            self.model = "closing-asr"
            self.closed = False
            created_providers.append(self)

        def append_audio(self, chunk: StreamChunk) -> list[TranscriptEvent]:
            if self.closed:
                return []
            if not chunk.final:
                return []
            self.closed = True
            return [TranscriptEvent(text=f"transcript:{chunk.stream_id}", final=True)]

        def cancel(self) -> None:
            self.closed = True

    monkeypatch.setattr(text_module, "build_asr_provider", lambda config: (ClosingAsrProvider(), None))

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))

    class Connection:
        device_id = "dev-recreate-asr"

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
            user_id="user-recreate-asr",
            producer_id=connection.device_id,
            payload={
                "device_id": connection.device_id,
                "auth": {"mode": "disabled"},
                "supports": {
                    "sensors": [{"type": "mic"}],
                    "actuators": [{"type": "speaker"}],
                },
                "properties": {"realtime_agent.audio_output": "actuator.speaker"},
            },
        ),
        connection,
    )
    first = app.open_input_stream(user_id="user-recreate-asr", producer_id=connection.device_id)
    second = app.open_input_stream(user_id="user-recreate-asr", producer_id=connection.device_id)

    for handle in (first, second):
        app.write_input_chunk(
            StreamChunk(
                user_id="user-recreate-asr",
                session_id=handle.session_id,
                stream_id=handle.stream_id,
                stream_type="sensor.mic",
                seq=0,
                payload=b"\x00\x00" * 320,
                final=True,
            )
        )

    messages_path = app.recorder.session_file(first.session_id, "messages.jsonl")
    messages = [json.loads(line) for line in messages_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    user_messages = [message["content"] for message in messages if message["role"] == "user"]
    assert len(created_providers) == 2
    assert user_messages == [f"transcript:{first.stream_id}", f"transcript:{second.stream_id}"]


def test_stream_lifecycle_idle_timeout_and_input_failed_events(tmp_path) -> None:
    """测试目标：覆盖输入 stream 的 failed、idle timeout 生命周期。

    测试方法：打开 sensor.mic stream 后分别触发 `fail_stream()` 和
    `close_idle_streams()`。
    预期结果：运行态进入 failed/closed，并写入对应生命周期事件。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), stream_idle_timeout_seconds=0.01))
    failed = app.open_input_stream(user_id="user-life", producer_id="dev-life")

    app.stream_service.fail_stream(failed.stream_id, reason="bad_header")

    assert app.stream_service.registry.get(failed.stream_id).state == "failed"

    idle = app.open_input_stream(user_id="user-life", producer_id="dev-life-2")
    idle.last_activity_at -= 10
    closed = app.stream_service.close_idle_streams(now=idle.last_activity_at + 10)

    assert [handle.stream_id for handle in closed] == [idle.stream_id]
    assert app.stream_service.registry.get(idle.stream_id).state == "closed"


def test_closed_input_stream_late_chunk_is_dropped(tmp_path) -> None:
    """测试目标：验证输入 stream 正常关闭后的迟到 chunk 不再升级为系统错误。

    测试方法：打开 `sensor.mic` 输入流并主动关闭，再写入同一 stream 的后续音频包。
    预期结果：写入过程不抛 `StreamNotOpenError`，不会进入音频处理，只记录 dropped 生命周期事件。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    handle = app.open_input_stream(user_id="user-late", producer_id="dev-late")
    app.stream_service.close_stream(handle.stream_id, reason="idle_timeout")

    app.write_input_chunk(
        StreamChunk(
            user_id="user-late",
            session_id=handle.session_id,
            stream_id=handle.stream_id,
            stream_type="sensor.mic",
            seq=1,
            payload=b"\x00\x00" * 320,
        )
    )

    events_path = app.recorder.session_file(handle.session_id, "stream-events.jsonl")
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    dropped = [event for event in events if event.get("event") == "stream.chunk.dropped"]
    assert dropped[-1]["stream_id"] == handle.stream_id
    assert dropped[-1]["reason"] == "input_stream_closed_late_chunk"


def test_closed_rgb_stream_final_chunk_is_stored_as_asset(tmp_path) -> None:
    """测试目标：验证相机单帧上传在 close/chunk 乱序时不会丢失。

    测试方法：打开 `sensor.rgb` 输入流后先关闭 stream，再发送不带 request_id 的
    final JPEG chunk，模拟 iOS 端先发关闭控制事件、后到二进制资产分片的竞态。
    预期结果：迟到 final chunk 被记录为 close 后收到，图片仍进入资产服务。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    handle = app.open_input_stream(
        user_id="user-late-photo",
        producer_id="dev-late-photo",
        stream_type="sensor.rgb",
        format=StreamFormat(codec="jpeg", sample_rate=1, channels=1, chunk_ms=1),
    )
    app.stream_service.close_stream(handle.stream_id, reason="camera_frame_uploaded")
    payload = b"\xff\xd8late-photo\xff\xd9"

    app.write_input_chunk(
        StreamChunk(
            user_id="user-late-photo",
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
            metadata={"capture_reason": "visual_sampler"},
        )
    )

    asset = app.asset_service.query_assets(user_id="user-late-photo", stream_type="sensor.rgb")[-1]
    assert asset.metadata["payload_size"] == len(payload)
    assert app.asset_service.wait_for_archive(asset.asset_id, timeout_seconds=1)
    assert Path(asset.uri).read_bytes() == payload

    events_path = app.recorder.session_file(handle.session_id, "stream-events.jsonl")
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    received_after_close = [event for event in events if event.get("event") == "stream.chunk.received_after_close"]
    assert received_after_close[-1]["stream_id"] == handle.stream_id
    assert received_after_close[-1]["reason"] == "input_stream_closed_final_chunk_race"


def test_mic_input_close_is_pushed_to_producer_device(tmp_path) -> None:
    """测试目标：服务端关闭麦克风输入流时，原生产端能收到关闭事件。

    测试方法：注册浏览器眼镜式设备，打开 `sensor.mic` 后由服务端按 idle_timeout
    关闭 stream。
    预期结果：设备连接收到 `stream.input.closed`，端侧可释放旧 stream_id 并创建下一段
    输入流。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))

    class Connection:
        device_id = "dev-browser-glass"

        def __init__(self) -> None:
            self.events = []

        def push_event(self, event: Event) -> None:
            self.events.append(event)

        def push_stream_chunk(self, chunk: StreamChunk) -> None:
            pass

    connection = Connection()
    response = app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id="user-browser-glass",
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
    assert response.event_name == "control.device.registered"
    handle = app.open_input_stream(user_id="user-browser-glass", producer_id=connection.device_id)

    app.stream_service.close_stream(handle.stream_id, reason="idle_timeout")

    close_events = [event for event in connection.events if event.event_name == "stream.input.closed"]
    assert len(close_events) == 1
    assert close_events[0].stream_id == handle.stream_id
    assert close_events[0].payload["reason"] == "idle_timeout"


def test_output_stream_freezes_consumers_for_chunks_close_and_cancel(tmp_path) -> None:
    """测试目标：确认 output stream 打开时冻结消费者。

    测试方法：先注册第一个 speaker 设备并打开 output stream，再注册第二个设备；
    随后写 chunk、close 和 cancel。
    预期结果：后续字节和生命周期事件只发送给打开 stream 时匹配到的第一个设备。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))

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
                    "properties": {"realtime_agent.audio_output": "actuator.speaker"},
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
    assert any(event.event_name == "stream.output.finish.requested" for event in first.events)
    assert any(event.event_name == "stream.output.cancel.requested" for event in first.events)
    assert not any(event.event_name == "stream.output.cancelled" for event in first.events)
    assert [event.event_name for event in second.events] == []


def test_asset_capture_rgb_stream_does_not_route_to_phone_display(tmp_path) -> None:
    """测试目标：确认实时视觉采样的单资产 RGB 流不会在 Task 前转发给手机。

    测试方法：注册一台带 RGB 的眼镜和一台订阅 RGB 输入的手机；眼镜上报带
    `request_id` 的 `stream.input.opened` 并发送一帧。
    预期结果：该流只进入服务端资产链路，不向手机下发 open/close 事件或视频 chunk。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))

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

    phone = Connection("dev-python-phone-preview")
    glass = Connection("dev-browser-glass-001")
    app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id="user-browser-glass-001",
            producer_id=phone.device_id,
            payload={
                "device_id": phone.device_id,
                "auth": {"mode": "disabled"},
                "properties": {
                    "endpoint.role.visual_display": True,
                    "endpoint.compute.vision": True,
                    "actuator.display.rgb": True,
                },
                "supports": {"sensors": [], "actuators": []},
            },
        ),
        phone,
    )
    app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id="user-browser-glass-001",
            producer_id=glass.device_id,
            payload={
                "device_id": glass.device_id,
                "auth": {"mode": "disabled"},
                "supports": {"sensors": [{"type": "rgb"}], "actuators": []},
            },
        ),
        glass,
    )

    app.publish_control_event(
        Event(
            event_name="stream.input.opened",
            user_id="user-browser-glass-001",
            producer_id=glass.device_id,
            session_id=glass.device_id,
            stream_id="stream_rgb_asset_probe",
            stream_type="sensor.rgb",
            payload={
                "stream_type": "sensor.rgb",
                "request_id": "asset_req_probe",
                "reason": "omni_visual_sampler",
                "format": {"codec": "jpeg", "sample_rate": 1, "channels": 1, "chunk_ms": 1},
            },
        )
    )
    app.write_input_chunk(
        StreamChunk(
            user_id="user-browser-glass-001",
            session_id=glass.device_id,
            stream_id="stream_rgb_asset_probe",
            stream_type="sensor.rgb",
            seq=0,
            payload=b"fake-jpeg",
            codec="jpeg",
            sample_rate=1,
            channels=1,
            duration_ms=1,
            final=True,
        )
    )
    app.publish_control_event(
        Event(
            event_name="stream.input.closed",
            user_id="user-browser-glass-001",
            producer_id=glass.device_id,
            session_id=glass.device_id,
            stream_id="stream_rgb_asset_probe",
            stream_type="sensor.rgb",
            payload={"stream_type": "sensor.rgb", "request_id": "asset_req_probe", "reason": "asset_done"},
        )
    )

    handle = app.stream_service.registry.get("stream_rgb_asset_probe")
    assert handle.consumer_device_ids == ()
    assert phone.chunks == []
    assert not any(event.stream_id == "stream_rgb_asset_probe" for event in phone.events)
