import json

import pytest

from realtime_agent import RealtimeAgentApp, RealtimeAgentConfig
from realtime_agent.protocol import Event, StreamChunk, StreamFormat
from tests.helpers.server_sdk_harness import install_text_turn_providers, register_audio_device


pytestmark = pytest.mark.sdk


def test_server_sdk_processes_text_turn_from_protocol_events_and_stream_chunks(tmp_path) -> None:
    """测试目标：验证 Server SDK 能从协议事件和麦克风 stream chunk 驱动完整Vision 回复。

    测试方法：注册测试设备，依次发送 wake、audio_session.opened、stream.input.opened
    和 final `sensor.mic` chunk；ASR/Vision provider 使用测试 harness 注入。
    预期结果：server 下发音频会话和输出 stream 事件，messages 记录用户转写和助手回复。
    """

    app = RealtimeAgentApp(
        RealtimeAgentConfig(
            runs_root=str(tmp_path / "runs"),
            agent_mode="vision",
            default_sensor_mic=StreamFormat(codec="pcm16le", sample_rate=16000, channels=1, chunk_ms=20),
            default_actuator_speaker=StreamFormat(codec="pcm16le", sample_rate=24000, channels=1, chunk_ms=40),
        )
    )
    user_id = "user-server-sdk-text-turn"
    device_id = "dev-server-sdk-text-turn"
    stream_id = "stream-server-sdk-mic"
    endpoint = register_audio_device(app, user_id=user_id, device_id=device_id)

    app.publish_control_event(
        Event(
            event_name="control.user.wake.detected",
            user_id=user_id,
            producer_id=device_id,
            payload={"reason": "test_wake"},
        )
    )
    app.publish_control_event(
        Event(
            event_name="control.audio_session.opened",
            user_id=user_id,
            producer_id=device_id,
            session_id=device_id,
            payload={"reason": "test_opened"},
        )
    )
    app.mark_stream_connection_opened(device_id)
    app.publish_control_event(
        Event(
            event_name="stream.input.opened",
            user_id=user_id,
            producer_id=device_id,
            session_id=device_id,
            stream_id=stream_id,
            stream_type="sensor.mic",
            payload={"stream_type": "sensor.mic", "format": app.config.default_sensor_mic.__dict__},
        )
    )
    asr_provider, vision_model = install_text_turn_providers(
        app,
        stream_id=stream_id,
        transcript="你是谁",
        response_deltas=["我是测试助手。"],
    )

    app.write_input_chunk(
        StreamChunk(
            user_id=user_id,
            session_id=device_id,
            stream_id=stream_id,
            stream_type="sensor.mic",
            seq=0,
            payload=b"\x01\x00" * 320,
            codec="pcm16le",
            sample_rate=16000,
            channels=1,
            duration_ms=20,
            final=True,
        )
    )

    event_names = endpoint.event_names()
    assert "control.audio_session.open.requested" in event_names
    assert "stream.output.open.requested" in event_names
    assert "stream.output.finish.requested" in event_names
    assert endpoint.chunks
    assert [chunk.stream_type for chunk in asr_provider.chunks] == ["sensor.mic"]
    assert vision_model.calls
    model_messages = vision_model.calls[-1]["messages"]
    assert model_messages[-1] == {"role": "user", "content": "你是谁"}

    session_dir = tmp_path / "runs" / user_id / device_id
    messages = [
        json.loads(line)
        for line in (session_dir / "messages.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(item.get("role") == "user" and item.get("content") == "你是谁" for item in messages)
    assert any(item.get("role") == "assistant" and "我是测试助手。" in item.get("content", "") for item in messages)


def test_server_sdk_rejects_mismatched_protocol_stream_chunk_before_agent_core(tmp_path) -> None:
    """测试目标：验证 Server SDK 在协议入口拦截 stream_type 不一致的二进制分片。

    测试方法：先用 `stream.input.opened` 注册 `sensor.mic` 输入流，再写入同 stream_id
    但 `stream_type=sensor.rgb` 的 chunk。
    预期结果：StreamService 抛出协议错误，Agent Core 不会收到该错误分片。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    user_id = "user-server-sdk-invalid-chunk"
    device_id = "dev-server-sdk-invalid-chunk"
    stream_id = "stream-server-sdk-invalid"
    register_audio_device(app, user_id=user_id, device_id=device_id)
    app.publish_control_event(
        Event(
            event_name="control.audio_session.opened",
            user_id=user_id,
            producer_id=device_id,
            session_id=device_id,
            payload={"reason": "test_opened"},
        )
    )
    app.publish_control_event(
        Event(
            event_name="stream.input.opened",
            user_id=user_id,
            producer_id=device_id,
            session_id=device_id,
            stream_id=stream_id,
            stream_type="sensor.mic",
            payload={"stream_type": "sensor.mic", "format": app.config.default_sensor_mic.__dict__},
        )
    )

    with pytest.raises(ValueError, match="stream_type does not match"):
        app.write_input_chunk(
            StreamChunk(
                user_id=user_id,
                session_id=device_id,
                stream_id=stream_id,
                stream_type="sensor.rgb",
                seq=0,
                payload=b"not-rgb",
                codec="pcm16le",
                sample_rate=16000,
                channels=1,
                duration_ms=20,
                final=True,
            )
        )
