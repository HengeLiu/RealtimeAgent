from __future__ import annotations

from collections.abc import Iterator

from realtime_agent.conversation.input.vad import SpeechBoundaryDelta, VoiceActivityBoundary
from realtime_agent.conversation.types import SpeechInputDelta
from realtime_agent.protocol import StreamChunk


class ServerVadSpeechInputBoundary:
    """基于服务端 VAD 的 conversation 语音输入边界。

    主要功能：把规范化音频持续转成 `audio_chunk`，并把 `VoiceActivityBoundary`
    产生的 speech 边界转成 `turn_started` / `turn_ended`。
    主要属性：`vad` 是只负责 speech 边界的检测器。
    """

    def __init__(self, vad: VoiceActivityBoundary | None = None) -> None:
        self.vad = vad or VoiceActivityBoundary()

    def append_audio(self, chunk: StreamChunk) -> Iterator[SpeechInputDelta]:
        """追加一片音频并输出标准语音输入增量。

        主要逻辑：每片音频先输出 `audio_chunk`，再输出该片触发的 turn 边界。
        参数：`chunk` 为规范化后的麦克风音频。
        返回值：语音输入增量迭代器。
        异常情况：沿用 VAD 底层异常。
        """

        yield SpeechInputDelta(
            kind="audio_chunk",
            session_id=chunk.session_id,
            user_id=chunk.user_id,
            stream_id=chunk.stream_id,
            audio=chunk,
        )
        for boundary in self.vad.append_audio(chunk):
            yield _boundary_to_speech_input_delta(boundary)

    def flush(self) -> Iterator[SpeechInputDelta]:
        """刷新输入边界。

        主要逻辑：把 VAD flush 产生的边界继续转成标准输入增量。
        返回值：语音输入增量迭代器。
        异常情况：无。
        """

        for boundary in self.vad.flush():
            yield _boundary_to_speech_input_delta(boundary)


def _boundary_to_speech_input_delta(boundary: SpeechBoundaryDelta) -> SpeechInputDelta:
    """把 VAD speech 边界转换为 conversation 输入增量。"""

    kind = "turn_started" if boundary.kind == "speech_started" else "turn_ended"
    return SpeechInputDelta(
        kind=kind,
        session_id=boundary.session_id,
        user_id=boundary.user_id,
        stream_id=boundary.stream_id,
        metadata={"speech_boundary": boundary.kind, **dict(boundary.metadata)},
    )
