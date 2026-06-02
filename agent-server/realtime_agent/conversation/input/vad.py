from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from realtime_agent.audio_pipeline import ServerVadProcessor
from realtime_agent.protocol import StreamChunk


SpeechBoundaryKind = Literal["speech_started", "speech_stopped"]


@dataclass(frozen=True, slots=True)
class SpeechBoundaryDelta:
    """语音活动边界增量。

    主要功能：表达 VAD 组件唯一允许输出的两类边界事件。
    主要属性：`kind` 是边界类型；`metadata` 只保存阈值、RMS 等诊断信息。
    """

    kind: SpeechBoundaryKind
    session_id: str
    user_id: str | None = None
    stream_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


class VoiceActivityBoundary:
    """conversation 语音活动边界检测器。

    主要功能：复用当前 `ServerVadProcessor` 的 RMS + 静音窗口逻辑，但只向下游输出
    `speech_started` 和 `speech_stopped`，不处理打断、视觉采样或模型提交。
    主要属性：`_processors` 按会话和 stream 保存现有服务端 VAD 处理器。
    """

    def __init__(self, *, threshold: int = 96, silence_timeout_ms: int = 600) -> None:
        self.threshold = threshold
        self.silence_timeout_ms = silence_timeout_ms
        self._processors: dict[tuple[str, str], ServerVadProcessor] = {}

    def append_audio(self, chunk: StreamChunk) -> list[SpeechBoundaryDelta]:
        """追加一片音频并返回 speech 边界。

        主要逻辑：调用旧 `ServerVadProcessor`，只把 `speech_started` 和
        `speech_stopped` 诊断转成边界对象。
        参数：`chunk` 为规范化后的 PCM16 麦克风音频。
        返回值：本片音频触发的边界列表，通常为空或包含一个事件。
        异常情况：沿用底层音频处理器异常。
        """

        result = self._processor_for(chunk).process(chunk)
        diagnostics = dict(result.diagnostics)
        deltas: list[SpeechBoundaryDelta] = []
        if diagnostics.get("speech_started"):
            deltas.append(
                SpeechBoundaryDelta(
                    kind="speech_started",
                    session_id=chunk.session_id,
                    user_id=chunk.user_id,
                    stream_id=chunk.stream_id,
                    metadata=diagnostics,
                )
            )
        if diagnostics.get("speech_stopped"):
            deltas.append(
                SpeechBoundaryDelta(
                    kind="speech_stopped",
                    session_id=chunk.session_id,
                    user_id=chunk.user_id,
                    stream_id=chunk.stream_id,
                    metadata=diagnostics,
                )
            )
        return deltas

    def _processor_for(self, chunk: StreamChunk) -> ServerVadProcessor:
        """返回当前音频流对应的 VAD 处理器。

        主要逻辑：不同 session 或 stream 的语音活跃状态不能共享；否则异常关闭或
        重连后的新输入可能继承旧 stream 的 speech_active，导致只出现 stopped
        而不再出现 started。
        参数：`chunk` 为当前音频片。
        返回值：该音频流独立的 `ServerVadProcessor`。
        异常情况：无。
        """

        key = (chunk.session_id, chunk.stream_id)
        processor = self._processors.get(key)
        if processor is None:
            processor = ServerVadProcessor(threshold=self.threshold, silence_timeout_ms=self.silence_timeout_ms)
            self._processors[key] = processor
        return processor

    def flush(self) -> list[SpeechBoundaryDelta]:
        """刷新边界检测器。

        主要逻辑：当前 RMS VAD 没有额外可 flush 的缓存，保留接口供后续 WebRTC VAD
        或 Silero VAD 接入。
        返回值：空列表。
        异常情况：无。
        """

        return []
