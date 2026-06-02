from __future__ import annotations

from pathlib import Path

import realtime_agent.agent_core.vision as vision_module
from realtime_agent.agent_core.providers import AsrProviderConfig, TranscriptEvent
from realtime_agent.conversation import (
    AgentOutputDelta,
    ConversationMemoryService,
    ConversationRuntimeConfig,
    SpeechInputDelta,
)
from realtime_agent.conversation.input import AsrSpeechInputBoundary, ServerVadSpeechInputBoundary, VoiceActivityBoundary
from realtime_agent.conversation.recorder import output_delta_record, speech_delta_record
from realtime_agent.observability import RunRecorder
from realtime_agent.protocol import StreamChunk


ROOT = Path(__file__).resolve().parents[4]
CONVERSATION_ROOT = ROOT / "agent-server/realtime_agent/conversation"


def test_conversation_package_keeps_memory_import_compatible(tmp_path) -> None:
    """测试目标：验证 `realtime_agent.conversation` 包化后保留旧记忆服务导入。

    测试方法：从新 package 入口导入 `ConversationMemoryService` 并写入一条消息。
    预期结果：旧导入路径可用，消息仍落入原有 `messages.jsonl`。
    """

    service = ConversationMemoryService(tmp_path / "runs")
    service.append_message(user_id="user-a", device_id="dev-a", message={"role": "user", "content": "你好"})

    assert service.legacy_messages_path(user_id="user-a", device_id="dev-a").exists()


def test_conversation_runtime_config_defaults_to_legacy() -> None:
    """测试目标：确认 Phase 0 新 runtime 配置默认保护旧链路。

    测试方法：直接构造 `ConversationRuntimeConfig`。
    预期结果：默认 runtime 是 `legacy`。
    """

    assert ConversationRuntimeConfig().runtime == "legacy"


def test_conversation_runtime_does_not_import_legacy_realtime_pipeline() -> None:
    """测试目标：验证 conversation runtime 不依赖旧 realtime pipeline 包装层。

    测试方法：扫描 `realtime_agent/conversation` 源码，检查是否导入
    `realtime_agent.realtime_pipeline`。
    预期结果：conversation 可以复用 OutputService 等基础服务，但不能反向依赖
    legacy pipeline helper。
    """

    source = "\n".join(path.read_text(encoding="utf-8") for path in CONVERSATION_ROOT.rglob("*.py"))

    assert "realtime_agent.realtime_pipeline" not in source


def test_speech_input_delta_record_does_not_embed_audio_bytes() -> None:
    """测试目标：验证语音输入增量记录不会把音频 bytes 写入事件 JSON。

    测试方法：构造包含音频 payload 的 `SpeechInputDelta` 并转换为记录结构。
    预期结果：记录只包含音频 seq，不包含原始 payload。
    """

    chunk = StreamChunk(
        stream_id="stream-a",
        stream_type="sensor.mic",
        user_id="user-a",
        session_id="session-a",
        seq=7,
        payload=b"\x01\x02\x03\x04",
        codec="pcm16le",
        sample_rate=16000,
        channels=1,
    )
    delta = SpeechInputDelta(kind="audio_chunk", session_id="session-a", user_id="user-a", stream_id="stream-a", audio=chunk)

    record = speech_delta_record(delta)

    assert record["kind"] == "audio_chunk"
    assert record["audio_seq"] == 7
    assert "payload" not in record
    assert b"\x01\x02" not in record.values()


def test_agent_output_delta_record_does_not_embed_audio_bytes() -> None:
    """测试目标：验证 Agent 输出增量记录不会把原生音频 bytes 写入事件 JSON。

    测试方法：构造包含原生音频的 `AgentOutputDelta` 并转换为记录结构。
    预期结果：记录只包含音频长度和采样率，不包含原始音频。
    """

    delta = AgentOutputDelta(kind="audio_chunk", session_id="session-a", output_id="out-a", audio=b"\x01\x02\x03", sample_rate_hz=24000)

    record = output_delta_record(delta)

    assert record["kind"] == "audio_chunk"
    assert record["audio_bytes"] == 3
    assert record["sample_rate_hz"] == 24000
    assert "audio" not in record
    assert b"\x01\x02\x03" not in record.values()


def test_voice_activity_boundary_outputs_only_speech_boundaries() -> None:
    """测试目标：验证 VoiceActivityBoundary 只输出 speech 边界。

    测试方法：输入一片高 RMS 音频和两片静音音频。
    预期结果：第一片触发 `speech_started`，静音窗口满足后触发 `speech_stopped`，
    输出中不包含打断、视觉采样或模型提交语义。
    """

    vad = VoiceActivityBoundary(threshold=10, silence_timeout_ms=40)
    speech = _mic_chunk(seq=1, payload=(1000).to_bytes(2, "little", signed=True) * 320)
    silence_1 = _mic_chunk(seq=2, payload=b"\x00\x00" * 320)
    silence_2 = _mic_chunk(seq=3, payload=b"\x00\x00" * 320)

    start = vad.append_audio(speech)
    mid = vad.append_audio(silence_1)
    stop = vad.append_audio(silence_2)

    assert [item.kind for item in start] == ["speech_started"]
    assert mid == []
    assert [item.kind for item in stop] == ["speech_stopped"]
    assert all("interrupt" not in item.metadata for item in start + stop)
    assert all("commit" not in item.metadata for item in start + stop)


def test_voice_activity_boundary_keeps_state_per_stream() -> None:
    """测试目标：验证 VAD 状态不会跨音频 stream 泄漏。

    测试方法：让第一个 stream 只触发 started 但不触发 stopped，然后直接输入第二个
    stream 的高 RMS 音频。
    预期结果：第二个 stream 仍能触发自己的 `speech_started`，不会继承第一个
    stream 的 speech_active 状态。
    """

    vad = VoiceActivityBoundary(threshold=10, silence_timeout_ms=40)
    speech = (1000).to_bytes(2, "little", signed=True) * 320

    first = vad.append_audio(_mic_chunk(seq=1, payload=speech, stream_id="stream-a"))
    second = vad.append_audio(_mic_chunk(seq=1, payload=speech, stream_id="stream-b"))

    assert [item.kind for item in first] == ["speech_started"]
    assert [item.kind for item in second] == ["speech_started"]
    assert first[0].stream_id == "stream-a"
    assert second[0].stream_id == "stream-b"


def test_server_vad_speech_input_boundary_emits_audio_and_turn_deltas() -> None:
    """测试目标：验证服务端 VAD 输入边界输出统一 SpeechInputDelta。

    测试方法：输入高 RMS 音频和足够长静音音频。
    预期结果：每片音频都有 `audio_chunk`，并额外输出 `turn_started` 和
    `turn_ended`。
    """

    boundary = ServerVadSpeechInputBoundary(VoiceActivityBoundary(threshold=10, silence_timeout_ms=40))
    chunks = [
        _mic_chunk(seq=1, payload=(1000).to_bytes(2, "little", signed=True) * 320),
        _mic_chunk(seq=2, payload=b"\x00\x00" * 320),
        _mic_chunk(seq=3, payload=b"\x00\x00" * 320),
    ]

    deltas = [delta for chunk in chunks for delta in boundary.append_audio(chunk)]

    assert [delta.kind for delta in deltas] == ["audio_chunk", "turn_started", "audio_chunk", "audio_chunk", "turn_ended"]
    assert deltas[0].audio is chunks[0]
    assert deltas[1].metadata["speech_boundary"] == "speech_started"
    assert deltas[-1].metadata["speech_boundary"] == "speech_stopped"


def test_asr_speech_input_boundary_maps_sentence_events_to_turn_deltas(tmp_path, monkeypatch) -> None:
    """测试目标：验证 ASR-backed 输入边界把句子事件映射为统一 SpeechInputDelta。

    测试方法：替换 ASR provider，让一片音频返回 sentence_begin、partial 和
    sentence_end/final。
    预期结果：输出顺序为 `audio_chunk`、`turn_started`、`asr_text_delta`、
    `turn_ended(final_text)`，并保留 ASR 诊断字段。
    """

    class SentenceAsrProvider:
        provider_name = "sentence-asr"
        model = "sentence-asr"

        def append_audio(self, chunk: StreamChunk) -> list[TranscriptEvent]:
            return [
                TranscriptEvent(text="", sentence_id=12, sentence_begin=True, begin_time_ms=100),
                TranscriptEvent(text="你是谁", sentence_id=12),
                TranscriptEvent(text="你是谁", final=True, sentence_id=12, sentence_end=True, end_time_ms=520),
            ]

        def cancel(self) -> None:
            pass

    monkeypatch.setattr(vision_module, "build_asr_provider", lambda config: (SentenceAsrProvider(), None))
    boundary = AsrSpeechInputBoundary(
        config=AsrProviderConfig(provider="sentence-asr", model="sentence-asr"),
        recorder=RunRecorder(tmp_path / "runs"),
    )

    deltas = list(boundary.append_audio(_mic_chunk(seq=1, payload=b"\x01\x00" * 320)))

    assert [delta.kind for delta in deltas] == ["audio_chunk", "turn_started", "asr_text_delta", "turn_ended"]
    assert deltas[1].metadata["asr_boundary"] == "sentence_begin"
    assert deltas[2].text_delta == "你是谁"
    assert deltas[3].final_text == "你是谁"
    assert deltas[3].metadata["asr_boundary"] == "sentence_end"


def _mic_chunk(*, seq: int, payload: bytes, stream_id: str = "stream-a") -> StreamChunk:
    """构造测试用麦克风音频 chunk。"""

    return StreamChunk(
        user_id="user-a",
        session_id="session-a",
        stream_id=stream_id,
        stream_type="sensor.mic",
        seq=seq,
        payload=payload,
        codec="pcm16le",
        sample_rate=16000,
        channels=1,
        duration_ms=20,
    )
