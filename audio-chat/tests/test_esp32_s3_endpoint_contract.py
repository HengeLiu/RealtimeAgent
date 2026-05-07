from __future__ import annotations

import asyncio
from pathlib import Path

from aiohttp import web

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.endpoints.esp32_aec import (
    DEFAULT_CHUNK_BYTES,
    Esp32AecEndpointState,
    Esp32S3EndpointConfig,
    NetworkEsp32S3Endpoint,
)
from audio_chat.protocol import Event
from audio_chat.server import AudioChatHttpServer


def test_esp32_s3_registration_payload_matches_event_stream_contract() -> None:
    """测试目标：验证 ESP32-S3 注册事件只声明协议能力和订阅。

    测试方法：构造 `Esp32AecEndpointState`，把 registration payload 放入正式 Event
    校验流程。
    预期结果：capability 表达 `sensor.mic`、`sensor.rgb` 和 `actuator.speaker`，
    订阅使用 event/filter，不出现固定 glass/phone 类型或 target_device 字段。
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
    assert payload["capabilities"]["streams.produce"] == ["sensor.mic", "sensor.rgb"]
    assert payload["capabilities"]["streams.consume"] == ["actuator.speaker"]
    assert payload["capabilities"]["audio.aec"] == "endpoint"
    assert payload["capabilities"]["audio.playback_reference"] == "endpoint_ring_buffer"
    assert payload["capabilities"]["sensor.rgb"] is True
    assert {"event": "control.audio_session.*"} in payload["subscriptions"]
    assert {"event": "stream.output.*", "filter": {"stream_type": "actuator.speaker"}} in payload["subscriptions"]
    assert {"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}} in payload["subscriptions"]
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


def test_esp32_s3_config_env_round_trip(tmp_path: Path) -> None:
    """测试目标：验证 ESP32-S3 `.env` 字段可被参考端解析。

    测试方法：写入与 `audio-chat.config.sync` 相同形态的 env 文件并读取。
    预期结果：server、user、device、auth、音频格式和 AEC 字段进入配置对象，注册
    payload 使用同一组值。
    """

    env_path = tmp_path / "esp32-s3.local.env"
    env_path.write_text(
        "\n".join(
            [
                "AUDIO_CHAT_SERVER_URL=http://10.0.0.2:8765",
                "AUDIO_CHAT_USER_ID=user-sync",
                "AUDIO_CHAT_DEVICE_ID=dev-sync-esp32",
                "AUDIO_CHAT_AUTH_MODE=static_token",
                "AUDIO_CHAT_AUTH_TOKEN=token-sync",
                "AUDIO_CHAT_AUDIO_SAMPLE_RATE=16000",
                "AUDIO_CHAT_AUDIO_CHANNELS=1",
                "AUDIO_CHAT_AUDIO_CHUNK_MS=20",
                "AUDIO_CHAT_AEC_MODE=endpoint",
                "AUDIO_CHAT_PLAYBACK_REFERENCE=endpoint_ring_buffer",
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
    assert payload["auth"]["token"] == "token-sync"
    assert payload["capabilities"]["audio.input"]["sample_rate"] == 16000
    assert payload["capabilities"]["audio.output"]["chunk_ms"] == 20


def test_network_esp32_s3_endpoint_completes_protocol_smoke(tmp_path: Path) -> None:
    """测试目标：验证 ESP32-S3 网络参考端走真实 `/ws/control` 和 `/ws/stream`。

    测试方法：启动 aiohttp server，运行 `NetworkEsp32S3Endpoint.run_once()`。
    预期结果：端侧完成注册、wake、session open、`sensor.mic` 上传、speaker 消费、
    playback 回执和 session close；诊断能对应同一 session。
    """

    async def run() -> None:
        audio_app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
        server = AudioChatHttpServer(audio_app)
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
        assert snapshot["capabilities"]["audio.aec"] == "endpoint"
        assert snapshot["capabilities"]["sensor.rgb"] is True

    asyncio.run(run())
