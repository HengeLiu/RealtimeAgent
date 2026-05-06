import threading
import time

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.config import load_yaml_config
from audio_chat.endpoints import Esp32AecEndpointState, PythonPlaybackEndpoint
from audio_chat.protocol import Event
from audio_chat.protocol import StreamChunk
from audio_chat.output import OutputIntent
from audio_chat.tools import GetOrRequestAssetTool, UserDeviceContext


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
    asset = GetOrRequestAssetTool().run(context, stream_type="sensor.rgb")

    assert asset is not None
    assert asset.stream_type == "sensor.rgb"
    assert asset.mime_type == "image/jpeg"
    assert asset.device_id == "dev-playback"
    assert any(event.event_name == "stream.control.configure.requested" for event in endpoint.events)


def test_esp32_aec_endpoint_declares_endpoint_aec_and_buffers_playback_reference() -> None:
    state = Esp32AecEndpointState(device_id="esp32-001", user_id="user-001")

    payload = state.registration_payload()
    state.on_wake_detected()
    state.on_playback_pcm(b"abc")
    state.enqueue_aec_mic_pcm(b"mic")

    assert payload["capabilities"]["audio.aec"] == "endpoint"
    assert state.sensor_mic_open is True
    assert state.aec_reference_ring.pop_all() == b"abc"
    assert list(state.mic_send_queue) == [b"mic"]


def test_yaml_config_loads_documented_sections() -> None:
    config = load_yaml_config("audio-chat/examples/minimal/server.yaml")

    assert config.server.port == 8765
    assert config.auth.mode == "disabled"
    assert config.agent.text.asr_provider == "mock"
    assert config.observability.runs_root == "runs/audio-chat"
    assert config.asset.request_timeout_seconds == 5


def test_device_handle_configure_stream_start_task_and_context_output(tmp_path) -> None:
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
                    {"event": "stream.output.*", "filter": {"stream_type": "actuator.speaker"}},
                ],
            },
        ),
        endpoint,
    )
    context = UserDeviceContext(user_id="user-device", app=app)
    handle = context.find_device("sensor.rgb")
    assert handle is not None

    handle.configure_stream(stream_type="sensor.rgb", mode="single")
    task = handle.start_task(task_type="rgb_window", params={"mode": "window"})
    task.stop(reason="test_done")
    context.submit_output(OutputIntent(user_id="user-device", session_id="sess-device"), "hello")

    event_names = [event.event_name for event in endpoint.events]
    assert "stream.control.configure.requested" in event_names
    assert "stream.output.open.requested" in event_names


def test_device_handle_operation_only_reaches_selected_device(tmp_path) -> None:
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
                    "capabilities": {"streams.produce": ["sensor.rgb"]},
                    "subscriptions": [
                        {"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}},
                        {"event": "task.state.changed"},
                    ],
                },
            ),
            endpoint,
        )

    context = UserDeviceContext(user_id="user-two", app=app)
    handle = context.find_device("sensor.rgb")
    assert handle is not None
    handle.configure_stream(stream_type="sensor.rgb", mode="single")
    handle.start_task(task_type="selected-only")

    assert [event.event_name for event in first.events].count("stream.control.configure.requested") == 1
    assert [event.event_name for event in second.events].count("stream.control.configure.requested") == 0
    assert [event.event_name for event in first.events].count("task.state.changed") == 1
    assert [event.event_name for event in second.events].count("task.state.changed") == 0


def test_device_handle_uses_device_command_service_not_control_private_api(tmp_path) -> None:
    """测试目标：确保 Tool/Task 只能通过 DeviceHandle 和内部 DeviceCommandService 操作设备。

    测试方法：替换 app.device_command_service 为 spy，调用 DeviceHandle.configure_stream。
    预期结果：spy 收到设备命令；业务上下文没有调用 ControlService 私有投递方法。
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
    calls = []

    class SpyDeviceCommandService:
        def send_to_device(self, *, device_id: str, event: Event):
            calls.append((device_id, event.event_name))

    app.device_command_service = SpyDeviceCommandService()
    context = UserDeviceContext(user_id="user-spy", app=app)
    handle = context.find_device("sensor.rgb")
    assert handle is not None

    handle.configure_stream(stream_type="sensor.rgb", mode="single")

    assert calls == [("dev-spy", "stream.control.configure.requested")]


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
        results[index] = app.asset_service.get_or_request_asset(
            user_id="user-concurrent",
            stream_type="sensor.rgb",
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
