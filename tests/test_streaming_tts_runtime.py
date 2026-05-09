from __future__ import annotations

import audioop
import json

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.output import AssistantTextDelta
from audio_chat.output.service import MockStreamingTTS, TtsProviderConfig, build_tts_provider
from audio_chat.protocol import Event, StreamChunk, StreamFormat


class Connection:
    """测试用端侧连接。"""

    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.events = []
        self.chunks = []

    def push_event(self, event: Event) -> None:
        """记录控制事件。"""

        self.events.append(event)

    def push_stream_chunk(self, chunk: StreamChunk) -> None:
        """记录输出音频。"""

        self.chunks.append(chunk)


def register_speaker(app: AudioChatApp, connection: Connection, user_id: str = "user-001") -> None:
    """注册一个可消费 speaker stream 的测试设备。"""

    app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id=user_id,
            producer_id=connection.device_id,
            payload={
                "device_id": connection.device_id,
                "auth": {"mode": "disabled"},
                "routes": [
                    {"event": "stream.output.*", "filter": {"stream_type": "actuator.speaker"}},
                ],
            },
        ),
        connection,
    )


def test_mock_streaming_tts_records_first_chunk_latency_metrics() -> None:
    """测试目标：验证 Streaming TTS 暴露首文本和首音频延迟指标。

    测试方法：直接使用 mock TTS 合成一段文本并读取 metrics。
    预期结果：`first_text_at`、`first_audio_at`、`first_chunk_latency_ms` 都有值。
    """

    tts = MockStreamingTTS(sample_rate_hz=16000)
    audio = tts.synthesize_delta("hello")
    metrics = tts.metrics()

    assert audio
    assert audioop.rms(audio, 2) > 1000
    assert metrics["first_text_at"] is not None
    assert metrics["first_audio_at"] is not None
    assert metrics["first_chunk_latency_ms"] is not None
    assert metrics["tts_first_audio_latency_ms"] == metrics["first_chunk_latency_ms"]


def test_output_service_persists_tts_latency_metrics_in_audio_delta_event(tmp_path) -> None:
    """测试目标：验证文本 delta 持续进入 TTS 后，runs 中能看到首包延迟指标。

    测试方法：注册 speaker 端侧，提交一段 assistant text delta。
    预期结果：端侧收到音频，`model-events.jsonl` 中记录 `first_chunk_latency_ms`。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), tts_provider="mock"))
    connection = Connection("dev-speaker")
    register_speaker(app, connection)

    app.output_service.on_assistant_text_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-tts", text="hello")
    )

    assert connection.chunks
    events = [
        json.loads(line)
        for line in (tmp_path / "runs" / "sessions" / "sess-tts" / "model-events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    audio_event = next(item for item in events if item.get("event") == "assistant_audio.delta")
    assert audio_event["tts"]["first_chunk_latency_ms"] is not None


def test_dashscope_tts_missing_key_falls_back_or_fails_explicitly(monkeypatch) -> None:
    """测试目标：验证真实 TTS provider 缺少 key 时的降级和禁用 fallback 行为。

    测试方法：清空 `DASHSCOPE_API_KEY`，分别构建允许 fallback 和禁止 fallback 的配置。
    预期结果：允许 fallback 时返回 mock 和降级原因；禁止 fallback 时抛出明确错误。
    """

    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    provider, reason = build_tts_provider(TtsProviderConfig(provider="dashscope", allow_mock_fallback=True))
    assert provider.provider_name == "mock"
    assert reason and "DASHSCOPE_API_KEY" in reason

    try:
        build_tts_provider(TtsProviderConfig(provider="dashscope", allow_mock_fallback=False))
    except RuntimeError as exc:
        assert "DASHSCOPE_API_KEY" in str(exc)
    else:  # pragma: no cover - 防止禁止 fallback 时静默降级
        raise AssertionError("expected RuntimeError when fallback is disabled")


def test_native_audio_delta_done_closes_stream_with_declared_sample_rate(tmp_path) -> None:
    """测试目标：验证原生 audio delta 走 stream，并在 done 后关闭。

    测试方法：提交 24k PCM 原生音频和 final done。
    预期结果：端侧收到 24k chunk，并收到 output close 事件。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    connection = Connection("dev-native")
    register_speaker(app, connection)
    fmt = StreamFormat(codec="pcm16le", sample_rate=24000, channels=1, chunk_ms=20)

    app.output_service.on_assistant_audio_delta(
        user_id="user-001",
        session_id="sess-native",
        audio=b"\x01\x00" * 480,
        format=fmt,
        metadata={"provider": "fake"},
    )
    app.output_service.on_assistant_audio_delta(
        user_id="user-001",
        session_id="sess-native",
        audio=b"",
        format=fmt,
        final=True,
        metadata={"provider": "fake"},
    )

    assert connection.chunks[0].sample_rate == 24000
    assert any(event.event_name == "stream.output.close.requested" for event in connection.events)
