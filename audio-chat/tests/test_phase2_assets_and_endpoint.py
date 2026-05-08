import asyncio
import threading
import time

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.config import load_yaml_config
from audio_chat_esp32_s3.esp32_aec import Esp32AecEndpointState
from audio_chat_python_glass.playback import PythonPlaybackEndpoint
from audio_chat.protocol import Event
from audio_chat.protocol import StreamChunk
from audio_chat.tools import RequestAssetTool, UserDeviceContext


def test_user_device_context_requests_sensor_rgb_asset_without_direct_device_target(tmp_path) -> None:
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    endpoint = PythonPlaybackEndpoint(app=app, user_id="user-asset", device_id="dev-playback")
    registration = Event(
        event_name="control.device.register.requested",
        user_id="user-asset",
        producer_id="dev-playback",
        payload={
            "device_id": "dev-playback",
            "auth": {"mode": "disabled"},
            "capabilities": {
                "streams.produce": ["sensor.mic", "sensor.rgb"],
                "streams.consume": ["actuator.speaker"],
                "sensor.rgb": True,
            },
            "subscriptions": [
                {"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}},
            ],
        },
    )
    app.register_device(registration, endpoint)

    context = UserDeviceContext(user_id="user-asset", app=app)
    asset = RequestAssetTool().run(context, stream_type="sensor.rgb")

    assert asset is not None
    assert asset.stream_type == "sensor.rgb"
    assert asset.mime_type == "image/jpeg"
    assert asset.device_id == "dev-playback"
    assert any(event.event_name == "stream.control.configure.requested" for event in endpoint.events)


def test_esp32_aec_endpoint_declares_endpoint_aec_and_buffers_playback_reference() -> None:
    state = Esp32AecEndpointState(device_id="esp32-001", user_id="user-001")

    payload = state.registration_payload()
    state.on_wake_detected()
    assert state.sensor_mic_open is False
    state.on_audio_session_open_requested("sess-001", stream_id="stream-mic-001")
    state.on_playback_pcm(b"abc")
    state.enqueue_aec_mic_pcm(b"mic")

    assert payload["capabilities"]["audio.aec"] == "endpoint"
    assert state.sensor_mic_open is True
    assert state.aec_reference_ring.pop_all() == b"abc"
    assert list(state.mic_send_queue) == [b"mic"]


def test_yaml_config_loads_documented_sections() -> None:
    config = load_yaml_config("examples/minimal/server.yaml")

    assert config.server.port == 8765
    assert config.auth.mode == "disabled"
    assert config.agent.text.asr_provider == "mock"
    assert config.observability.runs_root == "runs/audio-chat"
    assert config.asset.request_timeout_seconds == 5


def test_protocol_native_context_publish_event_and_submit_text(tmp_path) -> None:
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    endpoint = PythonPlaybackEndpoint(app=app, user_id="user-device", device_id="dev-device")
    app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id="user-device",
            producer_id="dev-device",
            payload={
                "device_id": "dev-device",
                "auth": {"mode": "disabled"},
                "capabilities": {"streams.produce": ["sensor.rgb"], "streams.consume": ["actuator.speaker"]},
                "subscriptions": [
                    {"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}},
                    {"event": "control.device.command.requested"},
                    {"event": "stream.output.*", "filter": {"stream_type": "actuator.speaker"}},
                ],
            },
        ),
        endpoint,
    )
    context = UserDeviceContext(user_id="user-device", app=app)

    result = context.publish_event(
        "stream.control.configure.requested",
        stream_type="sensor.rgb",
        payload={"mode": "single"},
        selection="first_available",
    )
    context.publish_event(
        "control.device.command.requested",
        payload={"command_name": "rgb_window.start", "params": {"mode": "window"}},
        selection="first_available",
    )
    context.submit_text("hello")

    event_names = [event.event_name for event in endpoint.events]
    assert result.delivered_count == 1
    assert "stream.control.configure.requested" in event_names
    assert "control.device.command.requested" in event_names
    assert "stream.output.open.requested" in event_names


def test_publish_event_first_available_reaches_one_matching_subscriber(tmp_path) -> None:
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    first = PythonPlaybackEndpoint(app=app, user_id="user-two", device_id="dev-first")
    second = PythonPlaybackEndpoint(app=app, user_id="user-two", device_id="dev-second")
    for endpoint in (first, second):
        app.register_device(
            Event(
                event_name="control.device.register.requested",
                user_id="user-two",
                producer_id=endpoint.device_id,
                payload={
                    "device_id": endpoint.device_id,
                    "auth": {"mode": "disabled"},
                    "capabilities": {"streams.produce": ["sensor.rgb"], "sensor.rgb": True},
                    "subscriptions": [
                        {"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}},
                        {"event": "control.device.command.requested"},
                    ],
                },
            ),
            endpoint,
        )

    context = UserDeviceContext(user_id="user-two", app=app)
    context.publish_event(
        "stream.control.configure.requested",
        stream_type="sensor.rgb",
        payload={"mode": "single"},
        selection="first_available",
    )
    context.publish_event(
        "control.device.command.requested",
        payload={"command_name": "selected-only"},
        selection="first_available",
    )

    assert [event.event_name for event in first.events].count("stream.control.configure.requested") == 1
    assert [event.event_name for event in second.events].count("stream.control.configure.requested") == 0
    assert [event.event_name for event in first.events].count("control.device.command.requested") == 1
    assert [event.event_name for event in second.events].count("control.device.command.requested") == 0


def test_context_publish_event_uses_protocol_matching_not_device_command_service(tmp_path) -> None:
    """测试目标：确保 Tool/Task 只能通过协议原生 Context API 操作端侧。

    测试方法：通过 `publish_event()` 发布 stream 配置请求。
    预期结果：端侧按订阅和能力收到事件；app 不再暴露旧 `device_command_service`。
    """
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    endpoint = PythonPlaybackEndpoint(app=app, user_id="user-spy", device_id="dev-spy")
    app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id="user-spy",
            producer_id="dev-spy",
            payload={
                "device_id": "dev-spy",
                "auth": {"mode": "disabled"},
                "capabilities": {"streams.produce": ["sensor.rgb"], "sensor.rgb": True},
                "subscriptions": [{"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}}],
            },
        ),
        endpoint,
    )
    context = UserDeviceContext(user_id="user-spy", app=app)

    result = context.publish_event(
        "stream.control.configure.requested",
        stream_type="sensor.rgb",
        payload={"mode": "single"},
        selection="first_available",
    )

    assert result.delivered_count == 1
    assert not hasattr(app, "device_command_service")
    assert [event.event_name for event in endpoint.events] == ["stream.control.configure.requested"]


def test_asset_request_id_prevents_concurrent_rgb_cross_talk(tmp_path) -> None:
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), asset_request_timeout_seconds=1))

    class ManualEndpoint:
        device_id = "dev-concurrent"

        def __init__(self) -> None:
            self.events = []
            self.chunks = []

        def push_event(self, event: Event) -> None:
            self.events.append(event)

        def push_stream_chunk(self, chunk) -> None:
            self.chunks.append(chunk)

    endpoint = ManualEndpoint()
    app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id="user-concurrent",
            producer_id="dev-concurrent",
            payload={
                "device_id": "dev-concurrent",
                "auth": {"mode": "disabled"},
                "capabilities": {"streams.produce": ["sensor.rgb"], "sensor.rgb": True},
                "subscriptions": [{"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}}],
            },
        ),
        endpoint,
    )
    results = [None, None]

    def request(index: int) -> None:
        results[index] = app.asset_service.request_asset(
            user_id="user-concurrent",
            stream_type="sensor.rgb",
            freshness_seconds=0,
            timeout_seconds=2,
        )

    threads = [threading.Thread(target=request, args=(index,)) for index in (0, 1)]
    for thread in threads:
        thread.start()
    deadline = time.time() + 1
    while time.time() < deadline:
        request_events = [event for event in endpoint.events if event.event_name == "stream.control.configure.requested"]
        if len(request_events) == 2:
            break
        time.sleep(0.01)
    request_events = [event for event in endpoint.events if event.event_name == "stream.control.configure.requested"]
    assert len(request_events) == 2

    second_request_id = request_events[1].payload["request_id"]
    first_request_id = request_events[0].payload["request_id"]
    for seq, request_id in ((2, second_request_id), (1, first_request_id)):
        handle = app.open_input_stream(user_id="user-concurrent", producer_id="dev-concurrent", stream_type="sensor.rgb")
        app.write_input_chunk(
            StreamChunk(
                user_id="user-concurrent",
                session_id=handle.session_id,
                stream_id=handle.stream_id,
                stream_type="sensor.rgb",
                seq=seq,
                payload=f"asset-{seq}".encode(),
                final=True,
                metadata={"request_id": request_id},
            )
        )
    for thread in threads:
        thread.join(timeout=2)

    assert results[0] is not None
    assert results[1] is not None
    assert {results[0].metadata["seq"], results[1].metadata["seq"]} == {1, 2}
    assert {results[0].metadata["request_id"], results[1].metadata["request_id"]} == {
        first_request_id,
        second_request_id,
    }
    assert first_request_id != second_request_id


def test_expired_asset_is_not_returned(tmp_path) -> None:
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    app.asset_service.default_ttl_seconds = -1
    handle = app.open_input_stream(user_id="user-expire", producer_id="dev-expire", stream_type="sensor.rgb")
    app.write_input_chunk(
        StreamChunk(
            user_id="user-expire",
            session_id=handle.session_id,
            stream_id=handle.stream_id,
            stream_type="sensor.rgb",
            seq=0,
            payload=b"\xff\xd8old\xff\xd9",
            final=True,
        )
    )

    assert app.asset_service.store.latest(user_id="user-expire", stream_type="sensor.rgb") is None


def test_asset_service_rejects_oversized_asset_payload(tmp_path) -> None:
    """测试目标：验证 Asset Service 执行 max_asset_bytes。

    测试方法：配置 4 字节资产上限，然后通过 sensor.rgb stream 上传 5 字节 payload。
    预期结果：服务拒绝缓存资产，避免大媒体绕过资产大小治理。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), asset_max_asset_bytes=4))
    handle = app.open_input_stream(user_id="user-asset-limit", producer_id="dev-camera", stream_type="sensor.rgb")

    try:
        app.write_input_chunk(
            StreamChunk(
                user_id="user-asset-limit",
                session_id=handle.session_id,
                stream_id=handle.stream_id,
                stream_type="sensor.rgb",
                seq=0,
                payload=b"12345",
                final=True,
            )
        )
    except ValueError as exc:
        assert "max_asset_bytes" in str(exc)
    else:
        raise AssertionError("oversized asset payload was accepted")


def test_asset_request_rejects_media_bytes_in_control_payload(tmp_path) -> None:
    """测试目标：确认资产请求不会把图片字节塞进控制事件。

    测试方法：调用 `request_asset()` 时在 configure_payload 中传入 bytes。
    预期结果：Asset Service 在发布事件前拒绝请求，端侧不会收到包含媒体字节的控制事件。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), asset_request_timeout_seconds=0.01))
    endpoint = PythonPlaybackEndpoint(app=app, user_id="user-no-bytes", device_id="dev-camera")
    app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id="user-no-bytes",
            producer_id="dev-camera",
            payload={
                "device_id": "dev-camera",
                "auth": {"mode": "disabled"},
                "capabilities": {"streams.produce": ["sensor.rgb"], "sensor.rgb": True},
                "subscriptions": [{"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}}],
            },
        ),
        endpoint,
    )

    try:
        app.asset_service.request_asset(
            user_id="user-no-bytes",
            stream_type="sensor.rgb",
            freshness_seconds=0,
            configure_payload={"inline_image": b"\xff\xd8bad\xff\xd9"},
        )
    except ValueError as exc:
        assert "media bytes" in str(exc)
    else:
        raise AssertionError("media bytes were accepted in control payload")

    assert endpoint.events == []


def test_watch_assets_yields_continuous_rgb_frames_by_correlation_id(tmp_path) -> None:
    """测试目标：验证连续 sensor.rgb 资产可按 correlation_id 被 Task 逐帧读取。

    测试方法：写入同一用户下两条相关帧和一条干扰帧，再通过 `watch_assets()` 监听
    指定 correlation_id。
    预期结果：只按写入顺序返回匹配 correlation_id 的两帧。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))

    def write_frame(seq: int, correlation_id: str) -> None:
        handle = app.open_input_stream(user_id="user-watch", producer_id="dev-camera", stream_type="sensor.rgb")
        app.write_input_chunk(
            StreamChunk(
                user_id="user-watch",
                session_id=handle.session_id,
                stream_id=handle.stream_id,
                stream_type="sensor.rgb",
                seq=seq,
                payload=f"frame-{seq}".encode(),
                final=True,
                metadata={"correlation_id": correlation_id},
            )
        )

    write_frame(1, "task-rgb-window")
    write_frame(99, "other-task")
    write_frame(2, "task-rgb-window")

    async def collect() -> list[int]:
        refs = []
        async for ref in app.asset_service.watch_assets(
            user_id="user-watch",
            stream_type="sensor.rgb",
            correlation_id="task-rgb-window",
            timeout_seconds=0.03,
        ):
            refs.append(ref)
        return [int(ref.metadata["seq"]) for ref in refs]

    assert asyncio.run(collect()) == [1, 2]
