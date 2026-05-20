from __future__ import annotations

import asyncio
import json
from pathlib import Path

from aiohttp import web

from realtime_agent.app import RealtimeAgentApp, RealtimeAgentConfig
from realtime_agent_esp32_s3.esp32_aec import (
    DEFAULT_CHUNK_BYTES,
    Esp32AecEndpointState,
    Esp32S3EndpointConfig,
    NetworkEsp32S3Endpoint,
)
from realtime_agent.protocol import Event
from realtime_agent.server import RealtimeAgentHttpServer


def test_esp32_s3_registration_payload_matches_event_stream_contract() -> None:
    """测试目标：验证 ESP32-S3 注册事件只提交调试属性和订阅。

    测试方法：构造 `Esp32AecEndpointState`，把 registration payload 放入正式 Event
    校验流程。
    预期结果：路由语义全部来自 event/filter 订阅，不出现固定 glass/phone 类型、
    target_device 字段或 capabilities 声明。
    """

    state = Esp32AecEndpointState(device_id="dev-esp32", user_id="user-esp32")
    payload = state.registration_payload()
    event = Event(
        event_name="control.device.register.requested",
        user_id="user-esp32",
        producer_id="dev-esp32",
        payload=payload,
    )

    assert event.to_dict()["payload"]["client_type"] == "esp32-s3"
    assert "capabilities" not in payload
    assert payload["properties"]["audio.aec"] == "endpoint"
    assert payload["properties"]["audio.playback_reference"] == "endpoint_ring_buffer"
    assert payload["properties"]["sensor.rgb.format"]["codec"] == "jpeg"
    assert payload["properties"]["direct.camera_source"] is True
    assert payload["properties"]["direct.camera.frame_format"] == "realtime_agent.direct_frame.v1"
    assert "routes" not in payload
    assert payload["properties"]["realtime_agent.audio_input"] == "sensor.mic"
    assert payload["properties"]["realtime_agent.audio_output"] == "actuator.speaker"
    assert payload["supports"]["sensors"][0]["type"] == "rgb"
    assert "target_device" not in str(payload)
    assert "phone" not in payload["client_type"]
    assert "glass" not in payload["client_type"]


def test_esp32_s3_state_opens_mic_only_after_wake_and_session_request() -> None:
    """测试目标：确认 ESP32-S3 不在启动后常驻上传麦克风。

    测试方法：按注册、wake、audio session open、speaker playback、session close 顺序
    推进端侧状态机。
    预期结果：wake 前和仅 wake 后都不允许 mic 入队；收到 open 请求后才允许上传；
    speaker PCM 同步进入 playback ring 和 AEC reference ring；关闭后释放 mic stream。
    """

    state = Esp32AecEndpointState(device_id="dev-esp32", user_id="user-esp32")

    assert state.enqueue_aec_mic_pcm(b"\x01" * DEFAULT_CHUNK_BYTES) is False
    assert state.diagnostics()["last_error_phase"] == "sensor_mic_not_open"
    state.on_wake_detected()
    assert state.wake_detected is True
    assert state.sensor_mic_open is False

    stream_id = state.on_audio_session_open_requested("sess-esp32", stream_id="stream-mic")
    assert stream_id == "stream-mic"
    assert state.sensor_mic_open is True
    assert state.enqueue_aec_mic_pcm(b"\x02" * DEFAULT_CHUNK_BYTES) is True

    state.on_playback_pcm(b"\x03" * 128)
    diagnostics = state.diagnostics()
    assert diagnostics["mic_chunks_sent"] == 1
    assert diagnostics["mic_bytes_sent"] == DEFAULT_CHUNK_BYTES
    assert diagnostics["speaker_chunks_received"] == 1
    assert diagnostics["playback_ring_bytes"] == 128
    assert diagnostics["aec_reference_bytes"] == 128

    state.on_audio_session_close_requested("server_closed")
    assert state.sensor_mic_open is False
    assert state.audio_session_open is False
    assert not state.mic_send_queue
    assert state.diagnostics()["close_reason"] == "server_closed"


def test_esp32_s3_state_handles_rgb_capture_as_stream_asset() -> None:
    """测试目标：验证 ESP32-S3 参考端响应 `sensor.rgb` 配置请求。

    测试方法：直接推进端侧状态机中的 RGB 配置处理。
    预期结果：端侧只返回 JPEG bytes 供 stream 发送，并把请求次数、帧数和字节数写入
    诊断摘要；禁用摄像头时返回失败诊断而不是假成功。
    """

    state = Esp32AecEndpointState(device_id="dev-esp32", user_id="user-esp32")

    frame = state.on_rgb_configure_requested({"mode": "single", "request_id": "req-rgb"})
    diagnostics = state.diagnostics()
    assert frame and frame.startswith(b"\xff\xd8")
    assert diagnostics["rgb_capture_requests"] == 1
    assert diagnostics["rgb_frames_sent"] == 1
    assert diagnostics["rgb_bytes_sent"] == len(frame)

    state.rgb_capture_enabled = False
    assert state.on_rgb_configure_requested({"mode": "single"}) is None
    assert state.diagnostics()["last_error_phase"] == "sensor_rgb_unavailable"


def test_esp32_s3_rgb_config_can_update_direct_camera_sink_uri() -> None:
    """测试目标：验证 server 可通过控制事件下发本次 iOS 相机直连地址。

    测试方法：构造带 `direct_camera_sink_uri` 的 `sensor.rgb` 配置请求。
    预期结果：ESP32 状态机更新直连地址，同时仍然返回 JPEG 给 `/ws/stream` 上传路径。
    """

    state = Esp32AecEndpointState(device_id="dev-esp32", user_id="user-esp32")
    frame = state.on_rgb_configure_requested(
        {
            "mode": "single",
            "direct_camera_sink_uri": "ws://10.0.0.3:9001/ws/camera",
        }
    )

    assert frame and frame.startswith(b"\xff\xd8")
    assert state.phone_camera_sink_ws_uri == "ws://10.0.0.3:9001/ws/camera"
    assert state.diagnostics()["direct_camera_sink_ws_uri"] == "ws://10.0.0.3:9001/ws/camera"


def test_esp32_s3_encodes_direct_rgb_frame_for_ios_sink() -> None:
    """测试目标：验证 ESP32-S3 参考端编码 realtime-agent 相机直连帧。

    测试方法：调用状态机的直连相机帧编码方法，拆出 4 字节大端 header 长度、
    JSON header 和 JPEG payload。
    预期结果：header 使用 `stream_type=sensor.rgb`、声明 payload_size，并保留
    stream_id、seq 和 timestamp_ms，iOS phone 可按同一格式解码。
    """

    state = Esp32AecEndpointState(device_id="dev-esp32", user_id="user-esp32")
    frame = b"\xff\xd8direct-camera\xff\xd9"
    encoded = state.encode_direct_rgb_frame(stream_id="stream-rgb", seq=7, payload=frame, timestamp_ms=123456)

    header_length = int.from_bytes(encoded[:4], "big")
    header = json.loads(encoded[4 : 4 + header_length].decode("utf-8"))
    payload = encoded[4 + header_length :]

    assert header["stream_type"] == "sensor.rgb"
    assert header["stream_id"] == "stream-rgb"
    assert header["seq"] == 7
    assert header["timestamp_ms"] == 123456
    assert header["codec"] == "jpeg"
    assert header["payload_size"] == len(frame)
    assert payload == frame


def test_esp32_s3_config_env_round_trip(tmp_path: Path) -> None:
    """测试目标：验证 ESP32-S3 `.env` 字段可被参考端解析。

    测试方法：写入与 `realtime-agent.config.sync` 相同形态的 env 文件并读取。
    预期结果：server、user、device、auth、音频格式和 AEC 字段进入配置对象，注册
    payload 使用同一组值。
    """

    env_path = tmp_path / "esp32-s3.local.env"
    env_path.write_text(
        "\n".join(
            [
                "REALTIME_AGENT_SERVER_URL=http://10.0.0.2:8765",
                "REALTIME_AGENT_USER_ID=user-sync",
                "REALTIME_AGENT_DEVICE_ID=dev-sync-esp32",
                "REALTIME_AGENT_AUTH_MODE=static_token",
                "REALTIME_AGENT_AUTH_TOKEN=token-sync",
                "REALTIME_AGENT_AUDIO_SAMPLE_RATE=16000",
                "REALTIME_AGENT_AUDIO_CHANNELS=1",
                "REALTIME_AGENT_AUDIO_CHUNK_MS=20",
                "REALTIME_AGENT_AEC_MODE=endpoint",
                "REALTIME_AGENT_PLAYBACK_REFERENCE=endpoint_ring_buffer",
                "REALTIME_AGENT_PHONE_CAMERA_SINK_WS_URI=ws://10.0.0.3:9001/ws/camera",
                "REALTIME_AGENT_PHONE_CAMERA_STREAM_INTERVAL_MS=250",
            ]
        ),
        encoding="utf-8",
    )

    config = Esp32S3EndpointConfig.from_env_file(env_path)
    state = Esp32AecEndpointState.from_config(config)
    payload = state.registration_payload()

    assert config.server_url == "http://10.0.0.2:8765"
    assert config.user_id == "user-sync"
    assert config.device_id == "dev-sync-esp32"
    assert config.auth_payload() == {"mode": "static_token", "token": "token-sync"}
    assert config.phone_camera_sink_ws_uri == "ws://10.0.0.3:9001/ws/camera"
    assert config.phone_camera_stream_interval_ms == 250
    assert payload["auth"]["token"] == "token-sync"
    assert payload["properties"]["audio.input"]["sample_rate"] == 16000
    assert payload["properties"]["audio.output"]["chunk_ms"] == 20
    assert payload["properties"]["direct.camera.default_sink_uri"] == "ws://10.0.0.3:9001/ws/camera"
    assert payload["properties"]["direct.camera.stream_interval_ms"] == 250


def test_network_esp32_s3_endpoint_completes_protocol_smoke(tmp_path: Path) -> None:
    """测试目标：验证 ESP32-S3 网络参考端走真实 `/ws/control` 和 `/ws/stream`。

    测试方法：启动 aiohttp server，运行 `NetworkEsp32S3Endpoint.run_once()`。
    预期结果：端侧完成注册、wake、session open、`sensor.mic` 上传、speaker 消费、
    playback 回执和 session close；诊断能对应同一 session。
    """

    async def run() -> None:
        audio_app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
        server = RealtimeAgentHttpServer(audio_app)
        runner = web.AppRunner(server.create_web_app())
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001
        try:
            endpoint = NetworkEsp32S3Endpoint(
                server_url=f"http://127.0.0.1:{port}",
                user_id="user-esp32",
                device_id="dev-esp32",
                runs_root=str(tmp_path / "runs"),
            )
            assert endpoint.state.sensor_mic_open is False
            result = await endpoint.run_once(audio_payload=b"\x00" * DEFAULT_CHUNK_BYTES)
        finally:
            await runner.cleanup()

        assert result["passed"] is True
        assert result["endpoint"] == "esp32-s3"
        assert result["diagnostics"]["mic_chunks_sent"] == 1
        assert result["diagnostics"]["speaker_chunks_received"] > 0
        assert result["diagnostics"]["aec_reference_bytes"] == result["diagnostics"]["speaker_bytes_received"]
        assert "control.device.registered" in result["event_names"]
        assert "stream.input.opened" in result["event_names"]
        assert "stream.output.started" in result["event_names"]
        assert "stream.output.finished" in result["event_names"]
        assert "control.audio_session.closed" in result["event_names"]
        snapshot = audio_app.control_service.build_device_snapshot("dev-esp32")
        assert snapshot is not None
        assert snapshot["client_type"] == "esp32-s3"
        assert snapshot["properties"]["audio.aec"] == "endpoint"
        assert snapshot["properties"]["sensor.rgb.format"]["codec"] == "jpeg"

    asyncio.run(run())
