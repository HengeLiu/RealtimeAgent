"""语音运行时共享状态模型。"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field

from infra.errors import ErrorCode, build_error
from protocol.media import MediaFrame
from runtime.audio_utils import PCM16StreamResampler, build_wav_bytes
from runtime.voice_constants import PLAYBACK_QUEUE_MAX, SERVER_SAMPLE_WIDTH_BYTES


@dataclass(slots=True)
class MessageEntry:
    """最小消息上下文条目。

    主要功能：
    1. 记录会话历史中一条可压缩的文本或资产消息。
    2. 为模型消息构造和运行快照提供轻量上下文。
    """

    role: str
    kind: str
    text: str
    asset_refs: list[str] = field(default_factory=list)
    created_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))


@dataclass(slots=True)
class VoiceTurnIntentDecision:
    """单轮语音的系统层意图裁决结果。

    主要属性：
    1. `intent`：当前轮系统意图，取值包括普通语音、停止对话和忽略。
    2. `reason`：裁决原因，用于日志和回放分析。
    3. `requires_photo`：兼容字段；视觉是否需要照片由模型通过 `capture_photo` 工具决定。
    4. `close_continuous_dialog`：是否应关闭端侧连续对话窗口。
    """

    intent: str
    reason: str
    requires_photo: bool = False
    close_continuous_dialog: bool = False


@dataclass(slots=True)
class SegmentBuffer:
    """单轮上行音频缓冲。

    主要功能：
    1. 校验并保存当前语音段的媒体帧。
    2. 保存旁路 ASR、Omni Realtime 和 AgentTurn 的关联状态。
    3. 提供 PCM 到 WAV 的封装能力，供 ASR 和运行产物落盘复用。
    """

    session_id: str
    stream_id: str
    segment_id: str
    sample_rate: int
    channels: int
    codec: str
    started_at_ms: int
    start_trigger: str = "unknown"
    payload: bytearray = field(default_factory=bytearray)
    frame_count: int = 0
    last_seq: int | None = None
    streaming_asr_session: "StreamingSpeechRecognitionSession | None" = None
    sidecar_asr_session: "StreamingSpeechRecognitionSession | None" = None
    sidecar_transcript_done: threading.Event = field(default_factory=threading.Event)
    sidecar_transcript_text: str = ""
    sidecar_transcript_source: str = ""
    sidecar_transcript_error: str | None = None
    sidecar_asr_metrics: dict[str, int | None] = field(default_factory=dict)
    omni_realtime_session: "OmniRealtimeStreamingSession | None" = None
    omni_realtime_context: "ReplySynthesisContext | None" = None
    omni_realtime_prepared: "PreparedNativeAudioReply | None" = None
    agent_turn: "AgentTurn | None" = None
    utterance_photo_capture_started: bool = False
    turn_intent: str = "unknown"
    turn_intent_reason: str = ""

    def append_frame(self, frame: MediaFrame, *, max_bytes: int) -> None:
        """追加并校验一帧音频。

        主要逻辑：
        1. 校验 frame_type、stream_id、segment_id 和 seq 连续性。
        2. 校验单段最大字节数，防止异常长音频撑爆内存。
        3. 校验通过后把 payload 写入本段缓冲。

        参数：
        1. `frame`：眼镜上传的媒体帧。
        2. `max_bytes`：单段允许的最大音频字节数。

        返回值：无。
        异常情况：
        1. 消息类型、流编号、段编号、序号或长度异常时抛出结构化错误。
        """

        header = frame.header
        if header.get("frame_type") != "audio_chunk":
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "仅支持 frame_type=audio_chunk",
                details={"frame_type": header.get("frame_type")},
            )
        if str(header.get("stream_id", "")) != self.stream_id:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "audio_chunk.stream_id 与当前段不一致",
                details={"expected": self.stream_id, "actual": header.get("stream_id")},
            )
        if str(header.get("segment_id", "")) != self.segment_id:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "audio_chunk.segment_id 与当前段不一致",
                details={"expected": self.segment_id, "actual": header.get("segment_id")},
            )

        seq = int(header.get("seq"))
        if self.last_seq is not None and seq != self.last_seq + 1:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "audio_chunk 序号不连续",
                details={"expected_seq": self.last_seq + 1, "actual_seq": seq},
            )
        self.last_seq = seq

        if len(self.payload) + len(frame.payload) > max_bytes:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "单轮音频过长，拒绝继续接收",
                details={
                    "segment_id": self.segment_id,
                    "max_segment_audio_bytes": max_bytes,
                },
            )

        self.payload.extend(frame.payload)
        self.frame_count += 1

    def duration_ms(self) -> int:
        """按采样率和声道数估算当前段时长。"""

        if self.sample_rate <= 0 or self.channels <= 0:
            return 0
        sample_count = len(self.payload) // (self.channels * SERVER_SAMPLE_WIDTH_BYTES)
        return int(sample_count * 1000 / self.sample_rate)

    def to_wav_bytes(self) -> bytes:
        """把当前 PCM 缓冲封装为 WAV 字节。"""

        return build_wav_bytes(bytes(self.payload), self.sample_rate, self.channels)


@dataclass(slots=True)
class PlaybackStreamContext:
    """单轮下行播放流状态。"""

    device_id: str
    session_id: str
    stream_id: str
    sample_rate: int
    channels: int
    source: str = "agent_reply"
    audio_source: str = "tts"
    priority: str = "normal"
    interrupt_policy: str = "never"
    resume_policy: str = "drop_interrupted"
    task_id: str | None = None
    intent_id: str = ""
    queue: queue.Queue[bytes | None] = field(default_factory=lambda: queue.Queue(maxsize=PLAYBACK_QUEUE_MAX))
    created_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    first_text_delta_at_ms: int | None = None
    first_audio_chunk_at_ms: int | None = None
    first_play_request_at_ms: int | None = None
    first_http_audio_chunk_at_ms: int | None = None
    play_requested: bool = False
    started: bool = False
    completed: bool = False
    failed: bool = False
    abort_event: threading.Event = field(default_factory=threading.Event)
    finished_event: threading.Event = field(default_factory=threading.Event)


@dataclass(slots=True)
class VoiceSessionController:
    """单设备最小语音会话编排器。"""

    device_id: str
    device_type: str
    session_id: str
    state: str = "opened"
    current_segment: SegmentBuffer | None = None
    current_playback: PlaybackStreamContext | None = None
    pending_playbacks: list[PlaybackStreamContext] = field(default_factory=list)
    message_context: list[MessageEntry] = field(default_factory=list)
    audio_connection_peer: str | None = None
    last_playback_stream_id: str | None = None
    last_playback_state: str | None = None
    last_playback_reason: str | None = None
    close_continuous_dialog_after_stream_id: str | None = None
    close_continuous_dialog_after_reason: str | None = None
    close_continuous_dialog_after_source: str | None = None
    persistent_omni_realtime_session: "OmniRealtimeStreamingSession | None" = None


@dataclass(slots=True)
class ReplySynthesisContext:
    """单条回复的下行音频上下文。

    主要功能：
    1. 保存一次回复对应的播放流和重采样状态。
    2. 把流式 TTS 或 Omni Realtime 产出的音频持续写入眼镜播放队列。
    """

    stream_id: str
    playback: PlaybackStreamContext
    audio_source: str = "tts"
    output_pcm: bytearray = field(default_factory=bytearray)
    resampler: PCM16StreamResampler | None = None
    finalized: bool = False


@dataclass(slots=True)
class ProgressAudioCacheEntry:
    """工具前置播报音频缓存条目。

    主要功能：
    1. 记录一段 `progress_message` 对应的本地 WAV 文件。
    2. 保存已经解码成 16k 单声道 PCM 的音频字节，便于工具调用时直接推入播放队列。
    """

    tool_name: str
    text: str
    wav_path: str
    metadata_path: str
    profile: dict[str, object]
    pcm_bytes: bytes
    created_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
