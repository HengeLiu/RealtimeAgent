from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.config import load_yaml_config
from audio_chat.endpoints import Esp32AecEndpointState, PythonPlaybackEndpoint
from audio_chat.protocol import Event
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
