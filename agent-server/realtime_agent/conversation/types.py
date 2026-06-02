from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from realtime_agent.protocol import StreamChunk


SpeechInputDeltaKind = Literal["audio_chunk", "asr_text_delta", "turn_started", "turn_ended"]
AgentOutputDeltaKind = Literal[
    "text_delta",
    "text_final",
    "audio_chunk",
    "output_started",
    "output_finished",
    "output_cancel_requested",
]


@dataclass(frozen=True, slots=True)
class SpeechInputDelta:
    """语音输入边界输出给 Agent Core 的标准增量。

    主要功能：把连续音频、ASR 文本增量和语音 turn 边界统一成下游可消费的
    输入对象。
    主要属性：`kind` 表示增量类型；`audio` 只在音频片段中使用；
    `text_delta/final_text` 只表示 ASR 文本，不使用 transcript 抽象名。
    """

    kind: SpeechInputDeltaKind
    session_id: str
    user_id: str | None = None
    stream_id: str | None = None
    audio: StreamChunk | None = None
    text_delta: str | None = None
    final_text: str | None = None
    turn_id: str | None = None
    monotonic_ms: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentOutputDelta:
    """Agent Core 输出给 conversation 输出适配层的标准增量。

    主要功能：区分文本、原生音频、输出开始、结束和取消请求，避免 Omni 与 VL
    在输出层形成两套播放仲裁逻辑。
    主要属性：`kind` 表示输出类型；`audio` 和 `sample_rate_hz` 用于原生音频；
    `text_delta` 用于文本或 TTS 输入。
    """

    kind: AgentOutputDeltaKind
    session_id: str
    output_id: str | None = None
    text_delta: str | None = None
    audio: bytes | None = None
    sample_rate_hz: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
