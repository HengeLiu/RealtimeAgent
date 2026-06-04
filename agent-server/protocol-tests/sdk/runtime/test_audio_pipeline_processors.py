from __future__ import annotations

from realtime_agent.audio_pipeline import (
    AudioPipeline,
    AudioPipelineConfig,
    FormatValidator,
    Pcm16Resampler,
    QualityVadProbe,
    ServerVadProcessor,
    VolumeProbe,
)
from realtime_agent.protocol import StreamChunk


class AgentCore:
    """测试用 Agent Core。

    主要功能：收集 Audio Pipeline 送入 Agent 的音频片。
    主要方法：`append_audio_event()` 记录 chunk。
    主要属性：`chunks` 保存收到的音频。
    """

    def __init__(self) -> None:
        self.chunks: list[StreamChunk] = []

    def append_audio_event(self, chunk: StreamChunk) -> None:
        """记录 Audio Pipeline 输出。"""

        self.chunks.append(chunk)


def _chunk(**overrides) -> StreamChunk:
    values = {
        "user_id": "user-audio",
        "session_id": "sess-audio",
        "stream_id": "stream-audio",
        "stream_type": "sensor.mic",
        "seq": 0,
        "payload": b"\x01\x00" * 320,
        "codec": "pcm16le",
        "sample_rate": 16000,
        "channels": 1,
    }
    values.update(overrides)
    return StreamChunk(**values)


def test_format_validator_rejects_non_mic_and_bad_codec() -> None:
    """测试目标：验证格式校验器拒绝非麦克风和非 PCM16 输入。

    测试方法：分别构造 `sensor.rgb` 和 `mulaw` 音频片调用 `FormatValidator`。
    预期结果：两个非法输入都抛出可读 `ValueError`。
    """

    validator = FormatValidator()

    for chunk in (_chunk(stream_type="sensor.rgb"), _chunk(codec="mulaw")):
        try:
            validator.process(chunk)
        except ValueError as exc:
            assert "sensor.mic" in str(exc) or "codec" in str(exc)
        else:
            raise AssertionError("FormatValidator accepted invalid chunk")


def test_pcm16_resampler_converts_sample_rate_to_pipeline_target() -> None:
    """测试目标：验证 PCM16 重采样器能把 8k 音频转换到 16k。

    测试方法：构造 8k 单声道 PCM16 chunk，并用默认目标格式处理。
    预期结果：输出 chunk 的 sample_rate 为 16000，metadata 标记已重采样。
    """

    result = Pcm16Resampler().process(_chunk(sample_rate=8000, payload=b"\x01\x00" * 160))

    assert result.chunk.sample_rate == 16000
    assert result.chunk.channels == 1
    assert result.chunk.metadata["audio_pipeline.resampled"] is True
    assert result.diagnostics["resampled"] is True


def test_volume_and_vad_probe_only_record_quality_statistics() -> None:
    """测试目标：确认音量与 VAD 探针只做诊断，不改写音频。

    测试方法：对同一段静音 PCM 依次运行 `VolumeProbe` 和 `QualityVadProbe`。
    预期结果：返回的 chunk 是原对象，诊断里能看出近似静音。
    """

    chunk = _chunk(payload=b"\x00\x00" * 320)
    volume = VolumeProbe().process(chunk)
    vad = QualityVadProbe(threshold=96).process(chunk)

    assert volume.chunk is chunk
    assert volume.diagnostics["rms"] == 0
    assert vad.chunk is chunk
    assert vad.diagnostics["near_silence"] is True
    assert vad.diagnostics["diagnostic_only"] is True


def test_server_vad_processor_emits_speech_boundaries() -> None:
    """测试目标：验证服务端 VAD 能输出语音开始和结束边界。

    测试方法：先输入高 RMS 音频，再输入足够长的静音片段。
    预期结果：第一片触发 speech_started，连续静音后触发 speech_stopped。
    """

    vad = ServerVadProcessor(threshold=96, silence_timeout_ms=40)

    started = vad.process(_chunk(payload=b"\xff\x7f" * 320, duration_ms=20))
    stopped = vad.process(_chunk(payload=b"\x00\x00" * 320, duration_ms=40))

    assert started.diagnostics["speech_started"] is True
    assert started.diagnostics["speech_active"] is True
    assert stopped.diagnostics["speech_stopped"] is True
    assert stopped.diagnostics["speech_active"] is False


def test_audio_pipeline_runs_processor_chain_before_audio_consumer() -> None:
    """测试目标：验证 Audio Pipeline 不再只有最小格式校验。

    测试方法：用 8k 输入音频跑完整 pipeline，并注入测试 Agent Core。
    预期结果：Agent 收到 16k 音频，pipeline 记录 format/resample/volume/vad 诊断。
    """

    agent = AgentCore()
    pipeline = AudioPipeline(audio_consumer=agent, config=AudioPipelineConfig(expected_sample_rate=16000))

    pipeline.process(_chunk(sample_rate=8000, payload=b"\x01\x00" * 160))

    assert agent.chunks[0].sample_rate == 16000
    processors = [item["processor"] for item in pipeline.last_diagnostics]
    assert processors == ["format_validator", "pcm16_resampler", "volume_probe", "quality_vad_probe"]


def test_audio_pipeline_server_vad_notifies_audio_consumer() -> None:
    """测试目标：验证 Audio Pipeline 会把服务端 VAD 边界通知给 Agent Core。

    测试方法：注入带 `on_speech_started` 钩子的测试 Agent，并启用 `server_only` VAD。
    预期结果：Agent 在收到音频前先收到 speech_started 事件。
    """

    class VadAwareAgent(AgentCore):
        """测试用 Agent Core，记录服务端 VAD 回调。"""

        def __init__(self) -> None:
            super().__init__()
            self.speech_started: list[dict] = []

        def on_speech_started(self, user_id: str, session_id: str, *, stream_id: str, reason: str, diagnostics: dict) -> None:
            """记录 speech_started 回调。"""

            self.speech_started.append(
                {
                    "user_id": user_id,
                    "session_id": session_id,
                    "stream_id": stream_id,
                    "reason": reason,
                    "diagnostics": diagnostics,
                    "chunks_seen": len(self.chunks),
                }
            )

    agent = VadAwareAgent()
    pipeline = AudioPipeline(audio_consumer=agent, config=AudioPipelineConfig(vad="server_only", vad_rms_threshold=96))

    pipeline.process(_chunk(payload=b"\xff\x7f" * 320))

    assert len(agent.speech_started) == 1
    assert agent.speech_started[0]["reason"] == "server_vad_speech_started"
    assert agent.speech_started[0]["chunks_seen"] == 0
    assert agent.chunks
