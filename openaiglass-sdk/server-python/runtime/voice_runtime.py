"""Phase C 语音会话运行时。"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import queue
import struct
import threading
import time
import uuid
import wave
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable

from agent_core import AgentFacade, AgentTurn, DerivedArtifact, MediaAssetRef
from agent_core.context import generate_id
from agent_core.runtime import NativeAudioReplyResult, PreparedNativeAudioReply
from backend_task_core import TaskEvent
from infra.config import ServerSettings
from infra.errors import AppError, ErrorCode, build_error
from infra.logging import LogContext, get_logger, log_debug, log_error, log_info
from protocol.media import MediaFrame
from runtime.notifications import NotificationCoordinator, NotificationRequest, NotificationSubmitResult
from runtime.playback_arbiter import PlaybackArbiter, PlaybackIntent
from runtime.realtime_voice import RealtimeModelAdapter, RealtimeVoiceRuntime
from runtime.task_event_bridge import TaskEventBridge
from runtime.text.text_dialog_state_machine import TextDialogStateMachine

SERVER_SAMPLE_RATE_HZ = 16000
SERVER_CHANNELS = 1
SERVER_SAMPLE_WIDTH_BYTES = 2
MODEL_OUTPUT_SAMPLE_RATE_HZ = 24000
PLAYBACK_QUEUE_MAX = 256
UTTERANCE_PHOTO_CAPTURE_TIMEOUT_MS = 10000
OMNI_SEMANTIC_VAD_NO_AUTO_RESPONSE = "semantic_vad_no_auto_response"
OMNI_SEMANTIC_VAD_AUTO_RESPONSE_GRACE_SECONDS = 3.0
VOICE_TURN_INTENT_SIDECAR_WAIT_SECONDS = 3.0
VOICE_TURN_SHORT_PENDING_ASR_MAX_MS = 1800
def _format_log_text(text: str, *, max_chars: int = 240) -> str:
    """格式化适合写入单行日志的文本。

    主要逻辑：
    1. 把换行和多余空白压缩成单个空格，避免一轮对话打散多行日志。
    2. 控制最大长度，防止异常长回复把关键链路日志淹没。

    参数：
    1. `text`：原始文本。
    2. `max_chars`：最多保留的字符数。

    返回值：
    1. 可直接写入日志的单行文本。

    异常情况：
    1. 本函数不抛出业务异常；空文本会返回空字符串。
    """

    compact = " ".join(text.split())
    if max_chars <= 0 or len(compact) <= max_chars:
        return compact
    return f"{compact[:max_chars]}..."


def _summarize_omni_server_event(message: dict[str, Any]) -> str:
    """生成 Omni Realtime 服务端事件的安全日志摘要。

    主要逻辑：
    1. 保留事件类型、响应编号、调用编号、工具名、文本等排障关键字段。
    2. 对音频 base64 分片只记录长度，避免 DEBUG 日志被大块音频数据淹没。
    3. 对嵌套对象做 JSON 压缩和截断，便于对照官方 server events 文档。
    """

    event_type = str(message.get("type") or "")
    summary: dict[str, Any] = {}
    for key, value in message.items():
        if key == "delta" and event_type == "response.audio.delta":
            summary["delta_base64_len"] = len(str(value or ""))
        elif key in {"delta", "text", "transcript", "arguments", "call_id", "item_id", "name"}:
            summary[key] = _format_log_text(str(value or ""), max_chars=160)
        elif key == "response" and isinstance(value, dict):
            summary[key] = {
                "id": value.get("id"),
                "status": value.get("status"),
                "output_count": len(value.get("output") or []) if isinstance(value.get("output"), list) else None,
            }
        elif key == "session" and isinstance(value, dict):
            tools = value.get("tools")
            instructions = str(value.get("instructions") or "")
            summary[key] = {
                "id": value.get("id"),
                "model": value.get("model"),
                "modalities": value.get("modalities"),
                "voice": value.get("voice"),
                "turn_detection": value.get("turn_detection"),
                "tool_count": len(tools) if isinstance(tools, list) else None,
                "instructions_len": len(instructions),
            }
        elif key == "error":
            summary[key] = _format_log_text(json.dumps(value, ensure_ascii=False, default=str), max_chars=300)
        elif key != "type":
            summary[key] = value
    return json.dumps(summary, ensure_ascii=False, default=str, separators=(",", ":"))


def _should_log_omni_server_event(event_type: str) -> bool:
    """判断 Omni Realtime server event 是否需要逐事件 DEBUG 日志。"""

    return event_type not in {
        "response.audio.delta",
    }


def _read_capture_photo_tool_image(output_payload: dict[str, Any]) -> bytes | None:
    """从 `capture_photo` 工具结果中读取图片字节，供 Omni Realtime 继续视觉回答。"""

    if not output_payload.get("ok"):
        return None
    data = output_payload.get("data")
    if not isinstance(data, dict):
        return None
    storage_uri = str(data.get("storage_uri") or "").strip()
    if not storage_uri or not os.path.isfile(storage_uri):
        return None
    with open(storage_uri, "rb") as image_file:
        return image_file.read()


@dataclass(slots=True)
class MessageEntry:
    """最小消息上下文条目。"""

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
    """单轮上行音频缓冲。"""

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
    omni_realtime_prepared: PreparedNativeAudioReply | None = None
    agent_turn: AgentTurn | None = None
    utterance_photo_capture_started: bool = False
    turn_intent: str = "unknown"
    turn_intent_reason: str = ""

    def append_frame(self, frame: MediaFrame, *, max_bytes: int) -> None:
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
        if self.sample_rate <= 0 or self.channels <= 0:
            return 0
        sample_count = len(self.payload) // (self.channels * SERVER_SAMPLE_WIDTH_BYTES)
        return int(sample_count * 1000 / self.sample_rate)

    def to_wav_bytes(self) -> bytes:
        return build_wav_bytes(bytes(self.payload), self.sample_rate, self.channels)


@dataclass(slots=True)
class PlaybackStreamContext:
    """单轮下行播放流。"""

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
class ModelChunk:
    """模型流式结果分片。"""

    text_delta: str = ""
    audio_pcm_bytes: bytes = b""
    sample_rate_hz: int = MODEL_OUTPUT_SAMPLE_RATE_HZ


@dataclass(slots=True)
class OmniRealtimeReplyResult:
    """Omni Realtime 单轮语音直出结果。"""

    assistant_text: str
    transcript: str
    response_id: str | None = None


class OmniRealtimeStreamingSession:
    """Omni Realtime 单轮流式输入会话。

    主要功能：
    1. 在用户开始说话时提前建立 Omni Realtime WebSocket。
    2. 用户说话过程中持续追加音频分片。
    3. 用户说完后追加可选图片、提交输入并等待模型音频响应。

    主要属性：
    1. `_conversation`：DashScope Omni Realtime 会话对象。
    2. `_done_event`：模型响应结束或失败事件。
    3. `_on_chunk`：模型音频分片回调。
    """

    def __init__(
        self,
        *,
        settings: ServerSettings,
        conversation: Any,
        done_event: threading.Event,
        error_box: list[str],
        assistant_text_parts: list[str],
        transcript_parts: list[str],
        response_id_box: list[str],
        request_started_at_ms_box: list[int],
        metrics_lock: threading.Lock,
        callback_state: dict[str, Any],
        pending_tool_count_box: list[int],
        on_chunk_box: list[Callable[[ModelChunk], None]],
        on_audio_done_box: list[Callable[[], None] | None],
        tool_handler_box: list[Callable[[dict[str, Any]], dict[str, Any]] | None],
        on_model_first_output_box: list[Callable[[str], None] | None],
        segment_id_box: list[str],
        stream_id_box: list[str],
        on_chunk: Callable[[ModelChunk], None],
        logger,
        session_id: str,
        device_id: str,
        segment_id: str,
        stream_id: str,
        connected_at_ms: int,
        connect_ms: int,
    ) -> None:
        """初始化流式输入会话。

        参数：
        1. `settings`：服务端配置。
        2. `conversation`：DashScope SDK 会话对象。
        3. `done_event/error_box/...`：回调共享状态。
        4. `on_chunk`：模型音频输出回调。
        5. `logger/session_id/device_id/...`：日志上下文。
        6. `connected_at_ms/connect_ms`：预连接完成时间和耗时。
        """

        self._settings = settings
        self._conversation = conversation
        self._done_event = done_event
        self._error_box = error_box
        self._assistant_text_parts = assistant_text_parts
        self._transcript_parts = transcript_parts
        self._response_id_box = response_id_box
        self._request_started_at_ms_box = request_started_at_ms_box
        self._metrics_lock = metrics_lock
        self._callback_state = callback_state
        self._pending_tool_count_box = pending_tool_count_box
        self._on_chunk_box = on_chunk_box
        self._on_audio_done_box = on_audio_done_box
        self._tool_handler_box = tool_handler_box
        self._on_model_first_output_box = on_model_first_output_box
        self._segment_id_box = segment_id_box
        self._stream_id_box = stream_id_box
        self._on_chunk = on_chunk
        self._logger = logger
        self._session_id = session_id
        self._device_id = device_id
        self._segment_id = segment_id
        self._stream_id = stream_id
        self._connected_at_ms = connected_at_ms
        self._connect_ms = connect_ms
        self._request_started_at_ms: int | None = None
        self._first_audio_append_at_ms: int | None = None
        self._audio_bytes = 0
        self._audio_frame_count = 0
        self._image_frame_count = 0
        self._closed = False

    def begin_turn(
        self,
        *,
        segment_id: str,
        stream_id: str,
        instructions: str,
        tools: list[dict[str, Any]] | None,
        tool_handler: Callable[[dict[str, Any]], dict[str, Any]] | None,
        on_chunk: Callable[[ModelChunk], None],
        on_audio_done: Callable[[], None] | None = None,
        on_model_first_output: Callable[[str], None] | None,
    ) -> None:
        """在同一条 Omni Realtime 长连接上开始新的用户轮次。

        主要逻辑：
        1. 重置上一轮的等待事件、文本累积、响应编号和首包指标。
        2. 更新本轮的播放回调、工具处理器和日志 segment/stream 标识。
        3. 通过 `session.update` 刷新当前 Agent-Core 生成的指令和工具 schema。

        参数：
        1. `segment_id/stream_id`：当前端侧语音段标识。
        2. `instructions/tools/tool_handler`：当前轮 Agent-Core 准备出的模型上下文。
        3. `on_chunk`：当前轮下行音频分片回调。
        4. `on_model_first_output`：当前轮模型首输出类型回调。

        异常情况：
        1. 长连接已关闭时抛出 `INVALID_MESSAGE`。
        2. 底层 `session.update` 失败时透传异常，由调用方关闭长连接并降级。
        """

        if self._closed:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "Omni Realtime 长连接已经关闭，不能开始新轮次",
                details={"segment_id": segment_id, "stream_id": stream_id},
            )
        self._done_event.clear()
        self._error_box.clear()
        self._assistant_text_parts.clear()
        self._transcript_parts.clear()
        self._response_id_box.clear()
        self._request_started_at_ms_box.clear()
        self._callback_state.update(
            {
                "response_created_at_ms": None,
                "first_audio_at_ms": None,
                "first_text_at_ms": None,
                "assistant_text_logged": False,
                "current_response_has_tool_call": False,
                "first_output_kind": None,
                "audio_done_notified": False,
            }
        )
        self._pending_tool_count_box[0] = 0
        self._on_chunk_box[0] = on_chunk
        self._on_audio_done_box[0] = on_audio_done
        self._tool_handler_box[0] = tool_handler
        self._on_model_first_output_box[0] = on_model_first_output
        self._segment_id_box[0] = segment_id
        self._stream_id_box[0] = stream_id
        self._segment_id = segment_id
        self._stream_id = stream_id
        self._request_started_at_ms = None
        self._first_audio_append_at_ms = None
        self._audio_bytes = 0
        self._audio_frame_count = 0
        self._image_frame_count = 0
        self._refresh_session(instructions=instructions, tools=tools)

    def _refresh_session(self, *, instructions: str, tools: list[dict[str, Any]] | None) -> None:
        """刷新当前 Omni Realtime session 的系统指令和工具列表。"""

        try:
            from dashscope.audio.qwen_omni import AudioFormat, MultiModality
        except ImportError as exc:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "缺少 dashscope 依赖，无法刷新 Omni Realtime 长连接",
                details={"hint": "请执行 uv sync 安装 dashscope"},
            ) from exc
        tool_session_kwargs = {"tools": tools, "tool_choice": "auto"} if tools else {}
        self._conversation.update_session(
            output_modalities=[MultiModality.TEXT, MultiModality.AUDIO],
            voice=self._settings.voice_model_voice,
            input_audio_format=AudioFormat.PCM_16000HZ_MONO_16BIT,
            output_audio_format=AudioFormat.PCM_24000HZ_MONO_16BIT,
            enable_input_audio_transcription=True,
            input_audio_transcription_model=self._settings.voice_asr_model_name,
            enable_turn_detection=self._settings.omni_turn_detection_enabled(),
            turn_detection_type=self._settings.voice_realtime_turn_detection_type,
            prefix_padding_ms=self._settings.voice_realtime_prefix_padding_ms,
            turn_detection_threshold=self._settings.voice_realtime_semantic_vad_threshold,
            turn_detection_silence_duration_ms=self._settings.voice_realtime_silence_duration_ms,
            instructions=instructions,
            **tool_session_kwargs,
        )
        log_debug(
            self._logger,
            (
                "Omni Realtime 长连接已刷新当前轮上下文 "
                f"segment_id={self._segment_id} stream_id={self._stream_id} tool_count={len(tools or [])}"
            ),
            LogContext(device_id=self._device_id, session_id=self._session_id, message_id="omni_realtime"),
        )

    @property
    def request_started_at_ms(self) -> int | None:
        """返回本轮提交响应请求的时间戳。"""

        return self._request_started_at_ms

    def append_audio(self, pcm_bytes: bytes) -> None:
        """追加一段用户上行音频。

        主要逻辑：
        1. 将 PCM 字节转成 base64。
        2. 调用 Omni Realtime `append_audio(...)`。
        3. 记录首段上行音频追加时间和累计字节数。
        """

        if not pcm_bytes:
            return
        self._conversation.append_audio(base64.b64encode(pcm_bytes).decode("ascii"))
        self._audio_bytes += len(pcm_bytes)
        self._audio_frame_count += 1
        if self._first_audio_append_at_ms is None:
            self._first_audio_append_at_ms = DashscopeOmniRealtimeReplyClient._now_ms()
            log_debug(
                self._logger,
                (
                    "Omni Realtime 首段上行音频已推送 "
                    f"bytes={len(pcm_bytes)} frame_count={self._audio_frame_count}"
                ),
                LogContext(device_id=self._device_id, session_id=self._session_id, message_id="omni_realtime"),
            )

    def has_audio(self) -> bool:
        """返回当前 Omni 会话是否已经追加过音频。

        返回值：
        1. `True` 表示已经至少向 Omni 发送过一段音频，可以追加图片。
        2. `False` 表示还没有音频，直接追加图片会被 Omni 拒绝。
        """

        return self._audio_bytes > 0

    def append_image_frames(self, image_frames: list[bytes]) -> int:
        """向 Omni Realtime 追加图片帧。

        主要逻辑：
        1. 确保当前会话已经发送过音频，符合 Omni Realtime 的输入顺序要求。
        2. 将图片字节转成 base64 后调用 `append_video(...)`。
        3. 返回实际追加的图片数量，便于调用方记录日志。

        参数：
        1. `image_frames`：待追加的图片原始字节列表。

        返回值：
        1. 实际成功追加的图片帧数量。

        异常情况：
        1. 尚未发送音频时抛出 `INVALID_MESSAGE`。
        2. 底层 WebSocket 发送失败时透传异常，由调用方决定是否降级。
        """

        if not image_frames:
            return 0
        if self._audio_bytes <= 0:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "Omni Realtime 追加图片前必须先追加音频",
                details={"segment_id": self._segment_id},
            )
        for image_bytes in image_frames:
            self._conversation.append_video(base64.b64encode(image_bytes).decode("ascii"))
            self._image_frame_count += 1
        log_debug(
            self._logger,
            (
                "Omni Realtime 已追加图片输入 "
                f"image_count={len(image_frames)} total_image_count={self._image_frame_count}"
            ),
            LogContext(device_id=self._device_id, session_id=self._session_id, message_id="omni_realtime"),
        )
        return len(image_frames)

    def finish(
        self,
        *,
        image_frames: list[bytes],
        instructions: str,
        segment_finished_at_ms: int,
    ) -> OmniRealtimeReplyResult:
        """提交本轮输入并等待 Omni Realtime 响应完成。

        主要逻辑：
        1. 音频已经在录音过程中持续追加。
        2. 语音结束后追加可选图片。
        3. `commit()` 后调用 `create_response(...)` 并等待响应完成。

        返回值：
        1. `OmniRealtimeReplyResult`。
        """

        if self._audio_bytes <= 0:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "Omni Realtime 输入音频为空",
                details={"segment_id": self._segment_id},
            )

        if self._settings.omni_turn_detection_enabled():
            appended_image_count = 0
            if image_frames:
                try:
                    appended_image_count = self.append_image_frames(image_frames)
                except Exception as exc:  # noqa: BLE001 - 迟到图片不能阻断 semantic_vad 响应等待
                    log_debug(
                        self._logger,
                        (
                            "Omni semantic_vad 等待阶段追加图片失败，继续等待自动响应 "
                            f"image_count={len(image_frames)} reason={exc!r}"
                        ),
                        LogContext(device_id=self._device_id, session_id=self._session_id, message_id="omni_realtime"),
                    )
            if not self._request_started_at_ms_box:
                self._done_event.wait(OMNI_SEMANTIC_VAD_AUTO_RESPONSE_GRACE_SECONDS)
            if not self._request_started_at_ms_box and not self._done_event.is_set():
                log_info(
                    self._logger,
                    (
                        "Omni semantic_vad 未自动提交，准备改用 segment_turn 重连兜底 "
                        f"model={self._settings.voice_omni_realtime_model_name} "
                        f"finish_to_check_ms="
                        f"{max(DashscopeOmniRealtimeReplyClient._now_ms() - segment_finished_at_ms, 0)} "
                        f"audio_bytes={self._audio_bytes} audio_frame_count={self._audio_frame_count} "
                        f"image_count={appended_image_count}"
                    ),
                    LogContext(device_id=self._device_id, session_id=self._session_id),
                )
                raise build_error(
                    ErrorCode.TIMEOUT,
                    "Omni semantic_vad 未自动提交",
                    retryable=True,
                    details={
                        "reason": OMNI_SEMANTIC_VAD_NO_AUTO_RESPONSE,
                        "segment_id": self._segment_id,
                        "stream_id": self._stream_id,
                        "fallback": "segment_turn_reconnect",
                    },
                )
            log_info(
                self._logger,
                (
                    "Omni semantic_vad 等待自动响应 "
                    f"model={self._settings.voice_omni_realtime_model_name} "
                    f"preconnected=true connect_ms={self._connect_ms} "
                    f"audio_bytes={self._audio_bytes} audio_frame_count={self._audio_frame_count} "
                    f"image_count={appended_image_count}"
                ),
                LogContext(device_id=self._device_id, session_id=self._session_id),
            )
            return self._wait_for_done()

        try:
            from dashscope.audio.qwen_omni import MultiModality
        except ImportError as exc:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "缺少 dashscope 依赖，无法启用 Omni Realtime 语音直出",
                details={"hint": "请执行 uv sync 安装 dashscope"},
            ) from exc

        for image_bytes in image_frames:
            self._conversation.append_video(base64.b64encode(image_bytes).decode("ascii"))
        self._conversation.commit()
        self._request_started_at_ms = DashscopeOmniRealtimeReplyClient._now_ms()
        self._request_started_at_ms_box.append(self._request_started_at_ms)
        self._conversation.create_response(
            instructions=instructions,
            output_modalities=[MultiModality.TEXT, MultiModality.AUDIO],
        )
        log_info(
            self._logger,
            (
                "Omni Realtime 请求已提交 "
                f"model={self._settings.voice_omni_realtime_model_name} "
                f"preconnected=true connect_ms={self._connect_ms} "
                f"finish_to_request_ms={max(self._request_started_at_ms - segment_finished_at_ms, 0)} "
                f"audio_bytes={self._audio_bytes} audio_frame_count={self._audio_frame_count} "
                f"image_count={len(image_frames)}"
            ),
            LogContext(device_id=self._device_id, session_id=self._session_id),
        )
        if not self._done_event.wait(max(5.0, self._settings.voice_model_timeout_ms / 1000)):
            raise build_error(
                ErrorCode.TIMEOUT,
                "Omni Realtime 等待响应完成超时",
                details={"segment_id": self._segment_id, "timeout_ms": self._settings.voice_model_timeout_ms},
            )
        if self._error_box:
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                "Omni Realtime 返回错误",
                details={"reason": self._error_box[-1], "segment_id": self._segment_id},
            )
        return self._build_result()

    def _wait_for_done(self) -> OmniRealtimeReplyResult:
        """等待 Omni Realtime 响应结束并构造结果。

        返回值：
        1. 当前轮 Omni 响应文本、转写和响应编号。

        异常情况：
        1. 等待超时或 Omni 返回错误时抛出结构化异常。
        """

        if not self._done_event.wait(max(5.0, self._settings.voice_model_timeout_ms / 1000)):
            raise build_error(
                ErrorCode.TIMEOUT,
                "Omni Realtime 等待响应完成超时",
                details={"segment_id": self._segment_id, "timeout_ms": self._settings.voice_model_timeout_ms},
            )
        if self._error_box:
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                "Omni Realtime 返回错误",
                details={"reason": self._error_box[-1], "segment_id": self._segment_id},
            )
        return self._build_result()

    def _build_result(self) -> OmniRealtimeReplyResult:
        """根据回调累积状态构造当前轮结果。"""

        return OmniRealtimeReplyResult(
            assistant_text="".join(self._assistant_text_parts).strip(),
            transcript="".join(self._transcript_parts).strip(),
            response_id=self._response_id_box[-1] if self._response_id_box else None,
        )

    def close(self, *, blocking: bool = True) -> None:
        """关闭 Omni Realtime 会话。

        参数：
            blocking: 是否在当前线程等待 DashScope SDK 的 `close()` 返回。播放流收口路径应使用
                `False`，避免底层 SDK 等待服务端超时而阻塞 HTTP 下行流 finalize。
        """

        if self._closed:
            return
        self._closed = True

        def _close_conversation() -> None:
            try:
                self._conversation.close()
            except Exception as exc:  # noqa: BLE001 - 关闭失败不能阻塞主链路
                log_debug(
                    self._logger,
                    f"Omni Realtime 会话关闭失败: reason={exc!r}",
                    LogContext(device_id=self._device_id, session_id=self._session_id, message_id="omni_realtime"),
                )

        if blocking:
            _close_conversation()
            return
        threading.Thread(
            target=_close_conversation,
            name=f"omni-realtime-close-{self._segment_id}",
            daemon=True,
        ).start()


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
    profile: dict[str, Any]
    pcm_bytes: bytes
    created_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))


class StreamingTtsSession:
    """流式 TTS 会话抽象接口。"""

    def push_text(self, text_delta: str) -> None:
        """推送一段新的文本增量。"""

        raise NotImplementedError

    def finish(self) -> None:
        """通知 TTS 当前文本已经全部推送完成。"""

        raise NotImplementedError


class BufferedStreamingTtsSession(StreamingTtsSession):
    """退化版流式 TTS 会话。

    主要功能：
    1. 在不支持真正增量合成时，先缓存所有文本。
    2. 在 `finish()` 时一次性调用旧的全文 TTS 生成音频。
    """

    def __init__(
        self,
        *,
        client: VoiceModelClient,
        settings: ServerSettings,
        on_chunk: Callable[[ModelChunk], None],
    ) -> None:
        self._client = client
        self._settings = settings
        self._on_chunk = on_chunk
        self._parts: list[str] = []

    def push_text(self, text_delta: str) -> None:
        if text_delta:
            self._parts.append(text_delta)

    def finish(self) -> None:
        text = "".join(self._parts).strip()
        if not text:
            text = "收到。"
        for chunk in self._client.stream_tts(settings=self._settings, text=text):
            self._on_chunk(chunk)


def _normalize_tts_event_payload(event: Any) -> dict[str, Any]:
    """归一化 TTS 事件对象。

    主要逻辑：
    1. 兼容字典、JSON 字符串和 SDK 对象三种输入。
    2. 抽取 `header` 与 `payload` 两层结构，便于统一解析。

    参数：
    1. `event`：DashScope 回调给出的事件对象。

    返回值：
    1. 统一后的事件字典；无法识别时返回空字典。
    """

    if event is None:
        return {}
    if isinstance(event, dict):
        return event
    if isinstance(event, str):
        try:
            parsed = json.loads(event)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    for method_name in ("to_dict", "model_dump", "dict"):
        method = getattr(event, method_name, None)
        if callable(method):
            try:
                payload = method()
            except TypeError:
                continue
            if isinstance(payload, dict):
                return payload

    header = _normalize_tts_event_payload(getattr(event, "header", None))
    payload = _normalize_tts_event_payload(getattr(event, "payload", None))
    if header or payload:
        return {"header": header, "payload": payload}
    return {}


def _extract_tts_event_summary(event: Any) -> dict[str, Any] | None:
    """提取需要保留的 TTS 关键事件摘要。

    主要逻辑：
    1. 只保留句子结束和任务完成两类节点。
    2. 提取任务编号、句子序号、句子文本与字符数，便于排查问题。

    参数：
    1. `event`：DashScope 回调给出的事件对象。

    返回值：
    1. 关键事件摘要；若当前事件无需记录则返回 `None`。
    """

    payload = _normalize_tts_event_payload(event)
    if not payload:
        return None

    header = payload.get("header")
    payload_body = payload.get("payload")
    if not isinstance(header, dict) or not isinstance(payload_body, dict):
        return None

    task_id = header.get("task_id")
    if header.get("event") == "task-finished":
        return {"kind": "task-finished", "task_id": task_id}

    output = payload_body.get("output")
    if not isinstance(output, dict):
        return None
    if output.get("type") != "sentence-end":
        return None

    sentence = output.get("sentence")
    usage = output.get("usage")
    if not isinstance(sentence, dict):
        sentence = {}
    if not isinstance(usage, dict):
        usage = {}

    return {
        "kind": "sentence-end",
        "task_id": task_id,
        "sentence_index": sentence.get("index"),
        "text": (output.get("original_text") or "").strip(),
        "characters": usage.get("characters"),
    }


class DashscopeCosyVoiceTtsSession(StreamingTtsSession):
    """基于百炼 CosyVoice WebSocket 的真正流式 TTS 会话。"""

    def __init__(
        self,
        *,
        settings: ServerSettings,
        on_chunk: Callable[[ModelChunk], None],
    ) -> None:
        try:
            import dashscope
            from dashscope.audio.tts_v2 import AudioFormat, ResultCallback, SpeechSynthesizer
        except ImportError as exc:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "缺少 dashscope 依赖，无法启用 CosyVoice 流式 TTS",
                details={"hint": "请执行 uv sync 安装 dashscope"},
            ) from exc

        self._settings = settings
        self._on_chunk = on_chunk
        self._error_message: str | None = None
        self._completed = threading.Event()
        self._closed = threading.Event()
        self._logger = get_logger("server.voice")
        self._task_id: str | None = None
        self._metrics_lock = threading.Lock()
        self._created_at_ms = self._now_ms()
        self._opened_at_ms: int | None = None
        self._first_text_push_started_at_ms: int | None = None
        self._first_text_push_returned_at_ms: int | None = None
        self._first_data_at_ms: int | None = None
        self._text_chars_pushed = 0
        self._text_push_count = 0
        self._text_chars_before_first_data: int | None = None
        self._text_push_count_before_first_data: int | None = None
        self._stream_start_lock = threading.Lock()
        self._prewarm_done = threading.Event()
        self._prewarm_started_at_ms: int | None = None
        self._prewarm_completed_at_ms: int | None = None
        self._prewarm_error: str | None = None
        self._prewarmed_stream = False

        dashscope.api_key = settings.dashscope_api_key
        dashscope.base_websocket_api_url = settings.tts_websocket_api_url

        completed_event = self._completed
        closed_event = self._closed
        error_box = self
        chunk_sink = self._on_chunk
        sample_rate_hz = settings.tts_sample_rate_hz

        class _Callback(ResultCallback):
            """CosyVoice 回调桥接器。"""

            def on_open(self):  # pragma: no cover - 真实联调路径
                error_box._mark_opened()
                return None

            def on_complete(self):  # pragma: no cover - 真实联调路径
                completed_event.set()

            def on_error(self, message: str):  # pragma: no cover - 真实联调路径
                error_box._error_message = message
                completed_event.set()

            def on_close(self):  # pragma: no cover - 真实联调路径
                closed_event.set()

            def on_event(self, message):  # pragma: no cover - 真实联调路径
                error_box._handle_event(message)

            def on_data(self, data: bytes) -> None:  # pragma: no cover - 真实联调路径
                if data:
                    error_box._mark_first_data(data)
                    chunk_sink(ModelChunk(audio_pcm_bytes=data, sample_rate_hz=sample_rate_hz))

        self._synthesizer = SpeechSynthesizer(
            model=settings.tts_model_name,
            voice=settings.tts_voice,
            format=AudioFormat.PCM_22050HZ_MONO_16BIT,
            callback=_Callback(),
        )
        threading.Thread(
            target=self._prewarm_stream,
            name="dashscope-tts-prewarm",
            daemon=True,
        ).start()

    def push_text(self, text_delta: str) -> None:
        if text_delta:
            self._wait_for_prewarm()
            started_at_ms = self._mark_text_push_started(text_delta)
            with self._stream_start_lock:
                self._synthesizer.streaming_call(text_delta)
            self._mark_text_push_returned(started_at_ms, text_delta)

    def finish(self) -> None:
        log_debug(
            self._logger,
            (
                "TTS 文本推送完成，等待音频收尾: "
                f"task_id={self._task_id or '<pending>'} "
                f"model={self._settings.tts_model_name} voice={self._settings.tts_voice}"
            ),
        )
        self._synthesizer.streaming_complete()
        completed = self._completed.wait(timeout=max(5.0, self._settings.voice_model_timeout_ms / 1000))
        if not completed:
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                "CosyVoice 流式 TTS 等待完成超时",
            )
        if self._error_message:
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                "CosyVoice 流式 TTS 调用失败",
                details={"reason": self._error_message},
            )

    def _handle_event(self, event: Any) -> None:
        """处理 DashScope 回调事件并输出精简日志。"""

        summary = _extract_tts_event_summary(event)
        if summary is None:
            return

        task_id = summary.get("task_id")
        if isinstance(task_id, str) and task_id.strip():
            self._task_id = task_id

        if summary["kind"] == "sentence-end":
            log_debug(
                self._logger,
                (
                    "TTS 句子完成: "
                    f"task_id={self._task_id or '<unknown>'} "
                    f"sentence_index={summary.get('sentence_index')} "
                    f"characters={summary.get('characters')} "
                    f"text={summary.get('text')}"
                ),
            )
            return

        if summary["kind"] == "task-finished":
            log_debug(
                self._logger,
                f"TTS 任务完成: task_id={self._task_id or '<unknown>'}",
            )

    def _prewarm_stream(self) -> None:
        """在后台提前打开 CosyVoice 流式任务。

        主要逻辑：
        1. DashScope `SpeechSynthesizer` 默认在首次 `streaming_call(...)` 时才连接
           WebSocket 并发送 run-task 请求。
        2. 这里复用 SDK 内部的 start-stream 能力，把连接和 run-task 握手前移到
           Agent 请求等待期间。
        3. 如果预热失败，不中断主链路；首次文本到来时仍按原逻辑触发建连，失败会由
           上层重建 TTS 会话。

        参数：无。
        返回值：无。
        异常情况：所有异常都会记录到调试日志并退化为首次文本触发。
        """

        started_at_ms = self._now_ms()
        with self._metrics_lock:
            self._prewarm_started_at_ms = started_at_ms

        start_stream = getattr(self._synthesizer, "_SpeechSynthesizer__start_stream", None)
        if not callable(start_stream):
            self._prewarm_error = "dashscope_start_stream_not_available"
            self._prewarm_done.set()
            log_debug(
                self._logger,
                "TTS 预热流启动跳过: 当前 dashscope SDK 未暴露 start_stream 内部能力",
                LogContext(session_id="tts", device_id="server", message_id="streaming_tts"),
            )
            return

        try:
            with self._stream_start_lock:
                start_stream()
                # start_stream 已经发送 run-task。把首次标记改为 false，避免后续
                # streaming_call 再次启动同一个任务。
                setattr(self._synthesizer, "_is_first", False)
            completed_at_ms = self._now_ms()
            with self._metrics_lock:
                self._prewarm_completed_at_ms = completed_at_ms
                self._prewarmed_stream = True
                opened_at_ms = self._opened_at_ms
            log_info(
                self._logger,
                (
                    "TTS 预热流已启动 "
                    f"prewarm_stream_cost_ms={self._latency_ms(started_at_ms, completed_at_ms)} "
                    f"session_create_to_prewarm_stream_ms={self._latency_ms(self._created_at_ms, completed_at_ms)} "
                    f"session_create_to_open_ms={self._latency_ms(self._created_at_ms, opened_at_ms)} "
                    f"model={self._settings.tts_model_name} voice={self._settings.tts_voice}"
                ),
                LogContext(session_id="tts", device_id="server", message_id="streaming_tts"),
            )
        except Exception as exc:  # pragma: no cover - 真实联调容错路径
            self._prewarm_error = repr(exc)
            log_debug(
                self._logger,
                f"TTS 预热流启动失败，回退到首次文本触发: reason={exc!r}",
                LogContext(session_id="tts", device_id="server", message_id="streaming_tts"),
            )
        finally:
            self._prewarm_done.set()

    def _wait_for_prewarm(self) -> None:
        """首个文本推送前等待后台预热结束，避免和预热线程重复启动任务。"""

        if self._prewarm_done.is_set():
            return
        wait_started_at_ms = self._now_ms()
        self._prewarm_done.wait(timeout=2.0)
        waited_ms = self._latency_ms(wait_started_at_ms, self._now_ms())
        if self._prewarm_done.is_set():
            log_debug(
                self._logger,
                (
                    "TTS 首次文本等待预热完成 "
                    f"wait_ms={waited_ms} prewarmed={self._prewarmed_stream} "
                    f"error={self._prewarm_error}"
                ),
                LogContext(session_id="tts", device_id="server", message_id="streaming_tts"),
            )
            return
        log_debug(
            self._logger,
            f"TTS 预热仍未完成，首次文本将等待同一启动锁: wait_ms={waited_ms}",
            LogContext(session_id="tts", device_id="server", message_id="streaming_tts"),
        )

    @staticmethod
    def _now_ms() -> int:
        """返回当前毫秒时间戳。"""

        return int(time.time() * 1000)

    @staticmethod
    def _latency_ms(start: int | None, end: int | None) -> int | None:
        """计算两个毫秒时间戳之间的非负耗时。"""

        if start is None or end is None:
            return None
        return max(end - start, 0)

    def _mark_opened(self) -> None:
        """记录 TTS WebSocket 打开时间。"""

        with self._metrics_lock:
            if self._opened_at_ms is not None:
                return
            self._opened_at_ms = self._now_ms()
            opened_at_ms = self._opened_at_ms
            created_at_ms = self._created_at_ms
        log_debug(
            self._logger,
            (
                "TTS WebSocket 已打开 "
                f"session_create_to_open_ms={self._latency_ms(created_at_ms, opened_at_ms)} "
                f"model={self._settings.tts_model_name} voice={self._settings.tts_voice}"
            ),
            LogContext(session_id="tts", device_id="server", message_id="streaming_tts"),
        )

    def _mark_text_push_started(self, text_delta: str) -> int:
        """记录一次文本增量推送开始，并返回开始时间。"""

        started_at_ms = self._now_ms()
        with self._metrics_lock:
            if self._first_text_push_started_at_ms is None:
                self._first_text_push_started_at_ms = started_at_ms
            self._text_chars_pushed += len(text_delta)
            self._text_push_count += 1
        return started_at_ms

    def _mark_text_push_returned(self, started_at_ms: int, text_delta: str) -> None:
        """记录首次 `streaming_call(...)` 返回耗时。"""

        returned_at_ms = self._now_ms()
        should_log = False
        with self._metrics_lock:
            if self._first_text_push_returned_at_ms is None:
                self._first_text_push_returned_at_ms = returned_at_ms
                should_log = True
                created_at_ms = self._created_at_ms
                opened_at_ms = self._opened_at_ms
                first_started_at_ms = self._first_text_push_started_at_ms
                text_chars_pushed = self._text_chars_pushed
                text_push_count = self._text_push_count
            else:
                created_at_ms = None
                opened_at_ms = None
                first_started_at_ms = None
                text_chars_pushed = 0
                text_push_count = 0
        if not should_log:
            return
        log_info(
            self._logger,
            (
                "TTS 首次文本已推送 "
                f"first_streaming_call_cost_ms={self._latency_ms(started_at_ms, returned_at_ms)} "
                f"session_create_to_first_push_ms={self._latency_ms(created_at_ms, first_started_at_ms)} "
                f"websocket_open_to_first_push_ms={self._latency_ms(opened_at_ms, first_started_at_ms)} "
                f"text_chars={len(text_delta)} total_text_chars={text_chars_pushed} push_count={text_push_count}"
            ),
            LogContext(session_id="tts", device_id="server", message_id="streaming_tts"),
        )

    def _mark_first_data(self, data: bytes) -> None:
        """记录 TTS 服务返回首段音频的接口级耗时。"""

        first_data_at_ms = self._now_ms()
        should_log = False
        with self._metrics_lock:
            if self._first_data_at_ms is None:
                self._first_data_at_ms = first_data_at_ms
                self._text_chars_before_first_data = self._text_chars_pushed
                self._text_push_count_before_first_data = self._text_push_count
                should_log = True
                created_at_ms = self._created_at_ms
                opened_at_ms = self._opened_at_ms
                first_push_started_at_ms = self._first_text_push_started_at_ms
                first_push_returned_at_ms = self._first_text_push_returned_at_ms
                text_chars = self._text_chars_before_first_data
                push_count = self._text_push_count_before_first_data
            else:
                created_at_ms = None
                opened_at_ms = None
                first_push_started_at_ms = None
                first_push_returned_at_ms = None
                text_chars = None
                push_count = None
        if not should_log:
            return
        log_info(
            self._logger,
            (
                "TTS 服务返回首段音频 "
                f"tts_first_audio_latency_ms={self._latency_ms(first_push_started_at_ms, first_data_at_ms)} "
                f"tts_first_audio_after_call_return_ms={self._latency_ms(first_push_returned_at_ms, first_data_at_ms)} "
                f"session_create_to_first_audio_ms={self._latency_ms(created_at_ms, first_data_at_ms)} "
                f"websocket_open_to_first_audio_ms={self._latency_ms(opened_at_ms, first_data_at_ms)} "
                f"text_chars_before_first_audio={text_chars} text_push_count_before_first_audio={push_count} "
                f"bytes={len(data)}"
            ),
            LogContext(session_id="tts", device_id="server", message_id="streaming_tts"),
        )


class VoiceModelClient:
    """语音模型客户端接口。"""

    def stream_reply(self, *, settings: ServerSettings, messages: list[dict[str, Any]]) -> Iterable[ModelChunk]:
        raise NotImplementedError

    def stream_tts(self, *, settings: ServerSettings, text: str) -> Iterable[ModelChunk]:
        """把文本转换为可播放语音流。

        主要逻辑：
        1. 当前默认通过同一语音模型执行“文本转音频”。
        2. 使用严格的“原样朗读”提示词，尽量避免改写文本。

        参数：
        1. `settings`：服务端配置。
        2. `text`：待播报文本。

        返回值：
        1. 流式音频分片。
        """

        prompt = text.strip() or "收到。"
        return self.stream_reply(
            settings=settings,
            messages=[
                {
                    "role": "system",
                    "content": "你现在只负责把用户提供的文本原样朗读成语音，不要补充解释，不要改写。",
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
        )

    def create_streaming_tts_session(
        self,
        *,
        settings: ServerSettings,
        on_chunk: Callable[[ModelChunk], None],
    ) -> StreamingTtsSession:
        """创建一个支持增量文本输入的 TTS 会话。

        主要逻辑：
        1. 默认返回退化版实现。
        2. 若子类支持真正的增量语音合成，可以覆盖该方法。
        """

        return BufferedStreamingTtsSession(
            client=self,
            settings=settings,
            on_chunk=on_chunk,
        )


class SpeechRecognitionClient:
    """语音转写客户端接口。

    主要功能：
    1. 接收一段完整用户语音。
    2. 返回该轮语音的转写文本。
    """

    def transcribe(self, *, settings: ServerSettings, input_wav: bytes) -> str:
        """把一段完整 WAV 音频转成文本。"""

        raise NotImplementedError

    def start_streaming_session(
        self,
        *,
        settings: ServerSettings,
        session_id: str,
        device_id: str,
        segment_id: str,
        stream_id: str,
        sample_rate_hz: int,
        channels: int,
        codec: str,
    ) -> "StreamingSpeechRecognitionSession | None":
        """启动实时 ASR 会话。

        主要逻辑：
        1. 默认客户端不支持实时 ASR，返回 `None`。
        2. 支持实时能力的子类应返回可持续接收 PCM 分片的会话对象。

        返回值：
        1. `StreamingSpeechRecognitionSession` 或 `None`。
        """

        return None


def _extract_recognition_sentence(result: Any) -> tuple[str, bool]:
    """从 DashScope RecognitionResult 中提取文本和句尾标记。

    主要逻辑：
    1. 读取 `result.get_sentence()` 返回的句子字典。
    2. 提取当前识别文本。
    3. 按官方 SDK 语义，用 `end_time` 是否存在判断当前句子是否结束。

    参数：
    1. `result`：DashScope 实时 ASR 回调传入的 RecognitionResult。

    返回值：
    1. `(text, is_sentence_end)`，文本为空表示当前事件没有可用识别结果。
    """

    sentence_getter = getattr(result, "get_sentence", None)
    if not callable(sentence_getter):
        return "", False
    try:
        sentence = sentence_getter()
    except Exception:
        return "", False
    if not isinstance(sentence, dict):
        return "", False
    text = sentence.get("text")
    if text is None:
        return "", False
    return str(text), sentence.get("end_time") is not None


class StreamingSpeechRecognitionSession:
    """实时 ASR 会话抽象。

    主要功能：
    1. 在眼镜上传音频帧时同步接收 PCM 分片。
    2. 在用户说完后尽快返回实时 ASR 已经得到的最终文本。
    """

    def append_audio(self, pcm_bytes: bytes) -> None:
        """追加一段 PCM 音频。"""

        raise NotImplementedError

    def finish(self) -> str:
        """结束音频输入并返回最终转写文本。"""

        raise NotImplementedError

    def metrics(self) -> dict[str, int | None]:
        """返回实时 ASR 计时指标。

        返回值：
        1. 指标名到毫秒值的字典；不支持的实现可以返回空字典。
        """

        return {}


class DashscopeVoiceModelClient(VoiceModelClient):
    """百炼兼容 Chat Completions 语音客户端。

    主要功能：
    1. 使用 OpenAI SDK 的兼容模式连接百炼服务。
    2. 发起流式多模态对话请求。
    3. 解析增量文本与增量音频分片。

    主要属性：
    1. `_sdk_client`：可选的 SDK 客户端注入点，便于测试时替换。
    """

    def __init__(self, sdk_client: Any | None = None) -> None:
        """初始化百炼模型客户端。

        参数：
        1. `sdk_client`：可选的 OpenAI SDK 客户端；测试时可注入假对象。
        """

        self._sdk_client = sdk_client

    def create_streaming_tts_session(
        self,
        *,
        settings: ServerSettings,
        on_chunk: Callable[[ModelChunk], None],
    ) -> StreamingTtsSession:
        """创建 DashScope CosyVoice 流式 TTS 会话。

        主要逻辑：
        1. 优先尝试使用官方 `dashscope.audio.tts_v2` WebSocket 能力。
        2. 若本地缺少依赖或初始化失败，则回退到兼容旧逻辑的全文 TTS。
        """

        try:
            session = DashscopeCosyVoiceTtsSession(
                settings=settings,
                on_chunk=on_chunk,
            )
            log_debug(
                get_logger("server.voice"),
                (
                    "CosyVoice 流式 TTS 初始化成功: "
                    f"model={settings.tts_model_name} voice={settings.tts_voice} "
                    f"sample_rate_hz={settings.tts_sample_rate_hz}"
                ),
                LogContext(session_id="tts", device_id="server", message_id="streaming_tts"),
            )
            return session
        except Exception as exc:  # pragma: no cover - 依赖缺失时走降级
            log_debug(
                get_logger("server.voice"),
                f"CosyVoice 流式 TTS 初始化失败，回退全文 TTS: reason={exc!r}",
                LogContext(session_id="tts", device_id="server", message_id="streaming_tts"),
            )
            return super().create_streaming_tts_session(settings=settings, on_chunk=on_chunk)

    def stream_reply(self, *, settings: ServerSettings, messages: list[dict[str, Any]]) -> Iterable[ModelChunk]:
        """调用百炼兼容接口并返回流式回复。

        主要逻辑：
        1. 校验 `DASHSCOPE_API_KEY` 是否存在。
        2. 使用 OpenAI SDK 创建 `chat.completions.create(..., stream=True)` 请求。
        3. 逐条解析 SDK 返回的分片，抽取文本增量与音频增量。

        参数：
        1. `settings`：服务端配置。
        2. `messages`：已组装完成的模型消息列表。

        返回值：
        1. 逐条产出 `ModelChunk`。

        异常情况：
        1. 缺少 API Key 时抛出 `AppError(INVALID_CONFIG)`。
        2. SDK 调用失败时抛出 `AppError(INTERNAL_ERROR)`，并附带状态码与响应体。
        """

        if not settings.dashscope_api_key.strip():
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "缺少 DASHSCOPE_API_KEY，无法执行真实语音对话",
            )

        client = self._sdk_client or self._create_sdk_client(settings)

        try:
            completion = client.chat.completions.create(
                model=settings.voice_model_name,
                messages=messages,
                modalities=["text", "audio"],
                audio={
                    "voice": settings.voice_model_voice,
                    "format": "wav",
                },
                stream=True,
                stream_options={"include_usage": True},
                timeout=settings.voice_model_timeout_ms / 1000,
            )
            for chunk in completion:
                parsed = self._parse_chunk(chunk)
                if parsed is not None:
                    yield parsed
        except Exception as exc:
            response_body = self._extract_sdk_error_body(exc)
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                "百炼模型接口调用失败",
                details={
                    "status": getattr(exc, "status_code", None),
                    "reason": str(exc),
                    "body": response_body,
                },
            ) from exc

    @staticmethod
    def _create_sdk_client(settings: ServerSettings) -> Any:
        """创建 OpenAI SDK 客户端。

        参数：
        1. `settings`：服务端配置。

        返回值：
        1. OpenAI SDK 客户端实例。

        异常情况：
        1. 未安装 `openai` 依赖时抛出 `AppError(INVALID_CONFIG)`。
        """

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "缺少 openai 依赖，无法通过 SDK 调用百炼兼容接口",
                details={"hint": "请执行 uv sync 或安装 openai 依赖"},
            ) from exc

        return OpenAI(
            api_key=settings.dashscope_api_key,
            base_url=settings.voice_model_base_url.rstrip("/"),
        )

    @staticmethod
    def _parse_chunk(event: Any) -> ModelChunk | None:
        """解析 SDK 流式分片。

        参数：
        1. `event`：OpenAI SDK 返回的单条流式对象。

        返回值：
        1. 有效分片时返回 `ModelChunk`，否则返回 `None`。
        """

        choices = getattr(event, "choices", None)
        if not isinstance(choices, list) or not choices:
            return None
        first_choice = choices[0]
        delta = getattr(first_choice, "delta", None)
        text_delta = extract_text_delta(getattr(delta, "content", None))
        audio = getattr(delta, "audio", None)
        audio_pcm = b""
        sample_rate = MODEL_OUTPUT_SAMPLE_RATE_HZ
        audio_data = read_attr_or_key(audio, "data")
        if isinstance(audio_data, str) and audio_data:
            audio_pcm = base64.b64decode(audio_data)
        sample_rate_raw = read_attr_or_key(audio, "sample_rate")
        if isinstance(sample_rate_raw, int) and sample_rate_raw > 0:
            sample_rate = sample_rate_raw
        if not text_delta and not audio_pcm:
            return None
        return ModelChunk(text_delta=text_delta, audio_pcm_bytes=audio_pcm, sample_rate_hz=sample_rate)

    @staticmethod
    def _extract_sdk_error_body(exc: Exception) -> str:
        """从 SDK 异常对象里尽量提取响应体文本。

        参数：
        1. `exc`：SDK 抛出的异常对象。

        返回值：
        1. 可读的响应体文本；若无法提取则返回空字符串。
        """

        body = getattr(exc, "body", None)
        if body is not None:
            return str(body)
        response = getattr(exc, "response", None)
        if response is None:
            return ""
        text = getattr(response, "text", None)
        if callable(text):
            try:
                return str(text())
            except Exception:
                return ""
        if text is not None:
            return str(text)
        return ""


class DashscopeOmniRealtimeReplyClient:
    """基于 Qwen Omni Realtime 的语音直出客户端。

    主要功能：
    1. 使用百炼 Omni Realtime WebSocket 把用户语音和可选图片直接送入全模态模型。
    2. 监听 `response.audio.delta`，把模型输出音频立即交给播放流。
    3. 保留文本转写和音频转写摘要，供运行时落盘和日志排障。

    主要属性：
    1. `_conversation_factory`：测试注入点；为空时使用 DashScope SDK 的
       `OmniRealtimeConversation`。
    """

    def __init__(self, conversation_factory: Callable[..., Any] | None = None) -> None:
        """初始化 Omni Realtime 客户端。

        参数：
        1. `conversation_factory`：可选会话工厂，单测可注入假 WebSocket 会话。
        """

        self._conversation_factory = conversation_factory
        self._logger = get_logger("server.voice")

    def start_streaming_reply(
        self,
        *,
        settings: ServerSettings,
        instructions: str,
        on_chunk: Callable[[ModelChunk], None],
        tools: list[dict[str, Any]] | None = None,
        tool_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        on_audio_done: Callable[[], None] | None = None,
        on_model_first_output: Callable[[str], None] | None = None,
        session_id: str,
        device_id: str,
        segment_id: str,
        stream_id: str,
    ) -> OmniRealtimeStreamingSession:
        """创建 Omni Realtime 流式输入会话。

        主要逻辑：
        1. 建立 Omni Realtime WebSocket。
        2. 配置音频输入、音频输出、转写和手动提交。
        3. 返回可持续追加音频的 `OmniRealtimeStreamingSession`。

        参数：
        1. `settings`：服务端配置。
        2. `instructions`：系统指令。
        3. `on_chunk`：音频分片回调。
        4. `tools`：可选的 Realtime function calling 工具 schema。
        5. `tool_handler`：工具调用处理函数，负责执行 SDK Tool 并返回可 JSON 序列化结果。
        6. `on_model_first_output`：模型首个输出类型回调，用于自动决定是否播报工具前置提示。
        7. `session_id/device_id/segment_id/stream_id`：日志上下文。

        返回值：
        1. 可追加音频、提交图片并等待响应的流式输入会话。

        异常情况：
        1. 缺少 API Key、缺少 dashscope 依赖或 WebSocket 建连失败时抛出结构化错误。
        """

        if not settings.dashscope_api_key.strip():
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "缺少 DASHSCOPE_API_KEY，无法执行 Omni Realtime 语音直出",
            )

        try:
            import dashscope
            from dashscope.audio.qwen_omni import (
                AudioFormat,
                MultiModality,
                OmniRealtimeCallback,
                OmniRealtimeConversation,
            )
        except ImportError as exc:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "缺少 dashscope 依赖，无法启用 Omni Realtime 语音直出",
                details={"hint": "请执行 uv sync 安装 dashscope"},
            ) from exc

        dashscope.api_key = settings.dashscope_api_key

        done_event = threading.Event()
        error_box: list[str] = []
        assistant_text_parts: list[str] = []
        transcript_parts: list[str] = []
        response_id_box: list[str] = []
        metrics_lock = threading.Lock()
        pending_tool_lock = threading.Lock()
        pending_tool_count_box = [0]
        request_started_at_ms_box: list[int] = []
        callback_state: dict[str, Any] = {
            "response_created_at_ms": None,
            "first_audio_at_ms": None,
            "first_text_at_ms": None,
            "assistant_text_logged": False,
            "current_response_has_tool_call": False,
            "first_output_kind": None,
            "audio_done_notified": False,
        }
        first_output_lock = threading.Lock()
        on_chunk_box = [on_chunk]
        on_audio_done_box = [on_audio_done]
        tool_handler_box = [tool_handler]
        on_model_first_output_box = [on_model_first_output]
        segment_id_box = [segment_id]
        stream_id_box = [stream_id]

        def _mark_first_model_output(kind: str) -> None:
            """记录 Omni 本轮第一个模型输出类型，并通知 Agent-Core 工具上下文。"""

            if kind not in {"tool_call", "text", "audio"}:
                return
            with first_output_lock:
                if callback_state.get("first_output_kind") is not None:
                    return
                callback_state["first_output_kind"] = kind
            callback = on_model_first_output_box[0]
            if callback is not None:
                try:
                    callback(kind)
                except Exception:
                    return

        def _notify_audio_done_once() -> None:
            """在 Omni 音频结束事件到达时只通知一次下行播放流收尾。"""

            if callback_state.get("audio_done_notified"):
                return
            callback_state["audio_done_notified"] = True
            callback = on_audio_done_box[0]
            if callback is None:
                return
            try:
                callback()
            except Exception as exc:  # noqa: BLE001 - 播放收尾失败要让等待方转入错误处理
                error_box.append(f"Omni Realtime 音频完成回调失败: {exc}")
                done_event.set()

        def _complete_tool_call(*, call_id: str, tool_name: str, arguments_text: str) -> None:
            """执行 Realtime 工具调用并把结果回填给 Omni。"""

            try:
                handler = tool_handler_box[0]
                if handler is None:
                    output_payload = {
                        "ok": False,
                        "error": {
                            "code": "TOOL_HANDLER_NOT_CONFIGURED",
                            "message": "当前运行时没有配置工具调用处理器",
                        },
                    }
                else:
                    output_payload = handler(
                        {
                            "call_id": call_id,
                            "name": tool_name,
                            "arguments": arguments_text,
                        }
                    )
                conversation.create_item(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps(output_payload, ensure_ascii=False, default=str),
                    }
                )
                if tool_name == "capture_photo":
                    image_bytes = _read_capture_photo_tool_image(output_payload)
                    if image_bytes:
                        conversation.append_video(base64.b64encode(image_bytes).decode("ascii"))
                        log_debug(
                            self_logger,
                            (
                                "Omni Realtime 已追加 capture_photo 工具图片 "
                                f"tool_name={tool_name} call_id={call_id} bytes={len(image_bytes)}"
                            ),
                            LogContext(device_id=device_id, session_id=session_id, message_id="omni_realtime"),
                        )
                with pending_tool_lock:
                    pending_tool_count_box[0] = max(pending_tool_count_box[0] - 1, 0)
                conversation.create_response(output_modalities=[MultiModality.TEXT, MultiModality.AUDIO])
                log_debug(
                    self_logger,
                    f"Omni Realtime 工具结果已回填 tool_name={tool_name} call_id={call_id}",
                    LogContext(device_id=device_id, session_id=session_id, message_id="omni_realtime"),
                )
            except Exception as exc:  # noqa: BLE001 - 工具桥异常需要结束当前 Realtime 响应
                with pending_tool_lock:
                    pending_tool_count_box[0] = max(pending_tool_count_box[0] - 1, 0)
                error_box.append(f"Omni Realtime 工具调用处理失败: {exc}")
                done_event.set()

        class _Callback(OmniRealtimeCallback):
            """Omni Realtime 回调桥接器。"""

            def on_open(self) -> None:  # pragma: no cover - 真实联调路径
                log_debug(
                    self_logger,
                    (
                        "Omni Realtime WebSocket 已打开 "
                        f"model={settings.voice_omni_realtime_model_name}"
                    ),
                    LogContext(device_id=device_id, session_id=session_id, message_id="omni_realtime"),
                )

            def on_close(self, close_status_code, close_msg) -> None:  # pragma: no cover - 真实联调路径
                if not done_event.is_set():
                    log_debug(
                        self_logger,
                        f"Omni Realtime WebSocket 已关闭 code={close_status_code} message={close_msg}",
                        LogContext(device_id=device_id, session_id=session_id, message_id="omni_realtime"),
                    )

            def on_event(self, message: dict[str, Any]) -> None:  # pragma: no cover - 真实联调路径
                event_type = str(message.get("type") or "")
                now_ms = DashscopeOmniRealtimeReplyClient._now_ms()
                if _should_log_omni_server_event(event_type):
                    log_debug(
                        self_logger,
                        (
                            "Omni Realtime server event "
                            f"type={event_type} payload={_summarize_omni_server_event(message)}"
                        ),
                        LogContext(device_id=device_id, session_id=session_id, message_id="omni_realtime"),
                    )
                if event_type == "response.created":
                    callback_state["current_response_has_tool_call"] = False
                    response = message.get("response")
                    if isinstance(response, dict):
                        response_id = str(response.get("id") or "")
                        if response_id:
                            response_id_box.append(response_id)
                    if not request_started_at_ms_box:
                        request_started_at_ms_box.append(now_ms)
                    callback_state["response_created_at_ms"] = now_ms
                    log_debug(
                        self_logger,
                        (
                            "Omni Realtime 响应已创建 "
                            f"response_id={response_id_box[-1] if response_id_box else '<unknown>'}"
                        ),
                        LogContext(device_id=device_id, session_id=session_id, message_id="omni_realtime"),
                    )
                    return

                if event_type in {"input_audio_buffer.speech_started", "input_audio_buffer.speech_stopped"}:
                    log_debug(
                        self_logger,
                        f"Omni Realtime 输入语音事件: {event_type}",
                        LogContext(device_id=device_id, session_id=session_id, message_id="omni_realtime"),
                    )
                    if event_type == "input_audio_buffer.speech_stopped" and not request_started_at_ms_box:
                        request_started_at_ms_box.append(now_ms)
                        log_info(
                            self_logger,
                            "Omni semantic_vad 检测到用户 turn 结束",
                            LogContext(device_id=device_id, session_id=session_id),
                        )
                    return

                if event_type == "input_audio_buffer.committed":
                    if not request_started_at_ms_box:
                        request_started_at_ms_box.append(now_ms)
                    log_debug(
                        self_logger,
                        "Omni Realtime 输入已自动提交",
                        LogContext(device_id=device_id, session_id=session_id, message_id="omni_realtime"),
                    )
                    return

                if event_type in {"response.audio_transcript.delta", "response.text.delta"}:
                    if callback_state.get("current_response_has_tool_call"):
                        with pending_tool_lock:
                            if pending_tool_count_box[0] <= 0:
                                callback_state["current_response_has_tool_call"] = False
                    delta = str(message.get("delta") or "")
                    if delta:
                        _mark_first_model_output("text")
                        assistant_text_parts.append(delta)
                        with metrics_lock:
                            if callback_state.get("first_text_at_ms") is None:
                                callback_state["first_text_at_ms"] = now_ms
                                log_info(
                                    self_logger,
                                    (
                                        "Omni Realtime 返回首个文本 "
                                        f"first_text_latency_ms="
                                        f"{DashscopeOmniRealtimeReplyClient._latency_ms(request_started_at_ms_box[-1] if request_started_at_ms_box else None, now_ms)} "
                                        f"response_create_to_first_text_ms="
                                        f"{DashscopeOmniRealtimeReplyClient._latency_ms(callback_state.get('response_created_at_ms'), now_ms)} "
                                        f"text_preview={delta[:24]!r}"
                                    ),
                                    LogContext(device_id=device_id, session_id=session_id),
                                )
                    return

                if event_type == "response.audio.delta":
                    if callback_state.get("current_response_has_tool_call"):
                        with pending_tool_lock:
                            if pending_tool_count_box[0] <= 0:
                                callback_state["current_response_has_tool_call"] = False
                    delta = str(message.get("delta") or "")
                    if not delta:
                        return
                    try:
                        audio_pcm = base64.b64decode(delta)
                    except Exception as exc:
                        error_box.append(f"response.audio.delta base64 解码失败: {exc}")
                        done_event.set()
                        return
                    if audio_pcm:
                        _mark_first_model_output("audio")
                    with metrics_lock:
                        if callback_state.get("first_audio_at_ms") is None:
                            callback_state["first_audio_at_ms"] = now_ms
                            log_info(
                                self_logger,
                                (
                                    "Omni Realtime 返回首段音频 "
                                    f"first_audio_latency_ms="
                                    f"{DashscopeOmniRealtimeReplyClient._latency_ms(request_started_at_ms_box[-1] if request_started_at_ms_box else None, now_ms)} "
                                    f"response_create_to_first_audio_ms="
                                    f"{DashscopeOmniRealtimeReplyClient._latency_ms(callback_state.get('response_created_at_ms'), now_ms)} "
                                    f"bytes={len(audio_pcm)}"
                                ),
                                LogContext(device_id=device_id, session_id=session_id),
                            )
                    on_chunk_box[0](ModelChunk(audio_pcm_bytes=audio_pcm, sample_rate_hz=MODEL_OUTPUT_SAMPLE_RATE_HZ))
                    return

                if event_type == "response.audio.done":
                    with pending_tool_lock:
                        has_pending_tool = pending_tool_count_box[0] > 0
                    if has_pending_tool or callback_state.get("current_response_has_tool_call"):
                        return
                    log_debug(
                        self_logger,
                        "Omni Realtime 音频输出完成",
                        LogContext(device_id=device_id, session_id=session_id, message_id="omni_realtime"),
                    )
                    _notify_audio_done_once()
                    done_event.set()
                    return

                if event_type == "response.function_call_arguments.done":
                    call_id = str(message.get("call_id") or message.get("item_id") or "")
                    tool_name = str(message.get("name") or "").strip()
                    arguments_text = str(message.get("arguments") or "{}")
                    if not tool_name:
                        error_box.append(f"Omni Realtime 工具调用缺少 name: {message}")
                        done_event.set()
                        return
                    _mark_first_model_output("tool_call")
                    callback_state["current_response_has_tool_call"] = True
                    with pending_tool_lock:
                        pending_tool_count_box[0] += 1
                    threading.Thread(
                        target=_complete_tool_call,
                        kwargs={
                            "call_id": call_id,
                            "tool_name": tool_name,
                            "arguments_text": arguments_text,
                        },
                        name=f"omni-realtime-tool-{tool_name}",
                        daemon=True,
                    ).start()
                    log_info(
                        self_logger,
                        f"Omni Realtime 工具调用请求 tool_name={tool_name} call_id={call_id}",
                        LogContext(device_id=device_id, session_id=session_id, message_id="omni_realtime"),
                    )
                    return

                if event_type in {"response.audio_transcript.done", "response.text.done"}:
                    final_text = str(message.get("transcript") or message.get("text") or "")
                    if final_text and not assistant_text_parts:
                        assistant_text_parts.append(final_text)
                    text_for_log = final_text or "".join(assistant_text_parts).strip()
                    if text_for_log and not callback_state.get("assistant_text_logged"):
                        callback_state["assistant_text_logged"] = True
                        log_info(
                            self_logger,
                            f"Omni Realtime 助手文本完成 text={_format_log_text(text_for_log)!r}",
                            LogContext(device_id=device_id, session_id=session_id),
                        )
                    return

                if event_type == "conversation.item.input_audio_transcription.completed":
                    transcript = str(message.get("transcript") or "")
                    if transcript:
                        transcript_parts.append(transcript)
                        log_info(
                            self_logger,
                            f"Omni Realtime 用户转写完成 transcript={_format_log_text(transcript)!r}",
                            LogContext(device_id=device_id, session_id=session_id),
                        )
                    return

                if event_type in {"response.done", "response.cancelled"}:
                    with pending_tool_lock:
                        has_pending_tool = pending_tool_count_box[0] > 0
                    if has_pending_tool or callback_state.get("current_response_has_tool_call"):
                        return
                    text_for_log = "".join(assistant_text_parts).strip()
                    if event_type == "response.done" and text_for_log and not callback_state.get("assistant_text_logged"):
                        callback_state["assistant_text_logged"] = True
                        log_info(
                            self_logger,
                            f"Omni Realtime 助手文本完成 text={_format_log_text(text_for_log)!r}",
                            LogContext(device_id=device_id, session_id=session_id),
                        )
                    if callback_state.get("first_audio_at_ms") is not None:
                        _notify_audio_done_once()
                    done_event.set()
                    return

                if event_type == "error":
                    error = message.get("error")
                    error_box.append(str(error if error is not None else message))
                    done_event.set()

        self_logger = self._logger
        callback = _Callback()
        factory = self._conversation_factory or OmniRealtimeConversation
        conversation = factory(
            model=settings.voice_omni_realtime_model_name,
            callback=callback,
            url=settings.voice_omni_realtime_url.rstrip("/"),
            api_key=settings.dashscope_api_key,
        )

        try:
            connect_started_at_ms = self._now_ms()
            conversation.connect()
            connected_at_ms = self._now_ms()
            tool_session_kwargs = {"tools": tools, "tool_choice": "auto"} if tools else {}
            conversation.update_session(
                output_modalities=[MultiModality.TEXT, MultiModality.AUDIO],
                voice=settings.voice_model_voice,
                input_audio_format=AudioFormat.PCM_16000HZ_MONO_16BIT,
                output_audio_format=AudioFormat.PCM_24000HZ_MONO_16BIT,
                enable_input_audio_transcription=True,
                input_audio_transcription_model=settings.voice_asr_model_name,
                enable_turn_detection=settings.omni_turn_detection_enabled(),
                turn_detection_type=settings.voice_realtime_turn_detection_type,
                prefix_padding_ms=settings.voice_realtime_prefix_padding_ms,
                turn_detection_threshold=settings.voice_realtime_semantic_vad_threshold,
                turn_detection_silence_duration_ms=settings.voice_realtime_silence_duration_ms,
                instructions=instructions,
                **tool_session_kwargs,
            )
            log_debug(
                self._logger,
                (
                    "Omni Realtime 预连接已建立 "
                    f"model={settings.voice_omni_realtime_model_name} "
                    f"connect_ms={max(connected_at_ms - connect_started_at_ms, 0)} "
                    f"turn_detection_enabled={settings.omni_turn_detection_enabled()} "
                    f"turn_detection_type={settings.voice_realtime_turn_detection_type}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )
            session = OmniRealtimeStreamingSession(
                settings=settings,
                conversation=conversation,
                done_event=done_event,
                error_box=error_box,
                assistant_text_parts=assistant_text_parts,
                transcript_parts=transcript_parts,
                response_id_box=response_id_box,
                request_started_at_ms_box=request_started_at_ms_box,
                metrics_lock=metrics_lock,
                callback_state=callback_state,
                pending_tool_count_box=pending_tool_count_box,
                on_chunk_box=on_chunk_box,
                on_audio_done_box=on_audio_done_box,
                tool_handler_box=tool_handler_box,
                on_model_first_output_box=on_model_first_output_box,
                segment_id_box=segment_id_box,
                stream_id_box=stream_id_box,
                on_chunk=on_chunk,
                logger=self._logger,
                session_id=session_id,
                device_id=device_id,
                segment_id=segment_id,
                stream_id=stream_id,
                connected_at_ms=connected_at_ms,
                connect_ms=max(connected_at_ms - connect_started_at_ms, 0),
            )
            return session
        except AppError:
            try:
                conversation.close()
            except Exception:
                pass
            raise
        except Exception as exc:
            try:
                conversation.close()
            except Exception:
                pass
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                "Omni Realtime 预连接失败",
                details={"reason": str(exc), "segment_id": segment_id, "stream_id": stream_id},
            ) from exc

    def run_reply(
        self,
        *,
        settings: ServerSettings,
        input_pcm: bytes,
        image_frames: list[bytes],
        instructions: str,
        on_chunk: Callable[[ModelChunk], None],
        tools: list[dict[str, Any]] | None = None,
        tool_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        on_model_first_output: Callable[[str], None] | None = None,
        session_id: str,
        device_id: str,
        segment_id: str,
        stream_id: str,
    ) -> OmniRealtimeReplyResult:
        """执行一次兼容旧调用方式的 Omni Realtime 语音直出。"""

        if not input_pcm:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "Omni Realtime 输入音频为空",
                details={"segment_id": segment_id},
            )
        session = self.start_streaming_reply(
            settings=settings,
            instructions=instructions,
            on_chunk=on_chunk,
            tools=tools,
            tool_handler=tool_handler,
            on_model_first_output=on_model_first_output,
            session_id=session_id,
            device_id=device_id,
            segment_id=segment_id,
            stream_id=stream_id,
        )
        try:
            session.append_audio(input_pcm)
            return session.finish(
                image_frames=image_frames,
                instructions=instructions,
                segment_finished_at_ms=self._now_ms(),
            )
        except AppError:
            raise
        except Exception as exc:
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                "Omni Realtime 语音直出调用失败",
                details={"reason": str(exc), "segment_id": segment_id, "stream_id": stream_id},
            ) from exc
        finally:
            session.close()

    def synthesize_text_audio(
        self,
        *,
        settings: ServerSettings,
        text: str,
        on_chunk: Callable[[ModelChunk], None],
        session_id: str,
        device_id: str,
        stream_id: str,
    ) -> OmniRealtimeReplyResult:
        """使用当前 Omni Realtime 模型生成一段提示文本的语音。

        主要逻辑：
        1. 创建独立的 Omni Realtime WebSocket 会话，避免干扰正在进行的用户回复会话。
        2. 不追加用户音频，只通过 `create_response(instructions=...)` 请求模型朗读固定提示语。
        3. 将 `response.audio.delta` 直接转换为 `ModelChunk`，复用统一下行播放流。

        参数：
            settings: 服务端模型和音色配置。
            text: 需要朗读的工具前置提示文本。
            on_chunk: 音频分片回调。
            session_id/device_id/stream_id: 日志上下文。

        返回值：
            Omni 生成的文本摘要和响应编号。

        异常情况：
            缺少 API Key、DashScope 依赖、WebSocket 调用失败或没有返回音频时抛出结构化错误。
        """

        prompt_text = text.strip()
        if not prompt_text:
            raise build_error(ErrorCode.INVALID_MESSAGE, "Omni Realtime 工具前置播报文本为空")
        if not settings.dashscope_api_key.strip():
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "缺少 DASHSCOPE_API_KEY，无法执行 Omni Realtime 工具前置播报",
            )

        try:
            import dashscope
            from dashscope.audio.qwen_omni import (
                AudioFormat,
                MultiModality,
                OmniRealtimeCallback,
                OmniRealtimeConversation,
            )
        except ImportError as exc:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "缺少 dashscope 依赖，无法启用 Omni Realtime 工具前置播报",
                details={"hint": "请执行 uv sync 安装 dashscope"},
            ) from exc

        dashscope.api_key = settings.dashscope_api_key
        done_event = threading.Event()
        error_box: list[str] = []
        assistant_text_parts: list[str] = []
        response_id_box: list[str] = []
        audio_bytes_box: list[int] = []
        created_at_ms_box: list[int] = []
        first_audio_at_ms_box: list[int] = []
        self_logger = self._logger

        class _Callback(OmniRealtimeCallback):
            """工具前置播报专用 Omni Realtime 回调桥。"""

            def on_open(self) -> None:  # pragma: no cover - 真实联调路径
                log_debug(
                    self_logger,
                    (
                        "Omni Realtime 工具前置播报 WebSocket 已打开 "
                        f"model={settings.voice_omni_realtime_model_name}"
                    ),
                    LogContext(device_id=device_id, session_id=session_id, message_id=stream_id),
                )

            def on_close(self, close_status_code, close_msg) -> None:  # pragma: no cover - 真实联调路径
                if not done_event.is_set():
                    log_debug(
                        self_logger,
                        (
                            "Omni Realtime 工具前置播报 WebSocket 已关闭 "
                            f"code={close_status_code} message={close_msg}"
                        ),
                        LogContext(device_id=device_id, session_id=session_id, message_id=stream_id),
                    )

            def on_event(self, message: dict[str, Any]) -> None:  # pragma: no cover - 真实联调路径
                event_type = str(message.get("type") or "")
                now_ms = DashscopeOmniRealtimeReplyClient._now_ms()
                log_debug(
                    self_logger,
                    (
                        "Omni Realtime 工具前置播报 server event "
                        f"type={event_type} payload={_summarize_omni_server_event(message)}"
                    ),
                    LogContext(device_id=device_id, session_id=session_id, message_id=stream_id),
                )
                if event_type == "response.created":
                    response = message.get("response")
                    if isinstance(response, dict):
                        response_id = str(response.get("id") or "")
                        if response_id:
                            response_id_box.append(response_id)
                    created_at_ms_box.append(now_ms)
                    return
                if event_type in {"response.audio_transcript.delta", "response.text.delta"}:
                    delta = str(message.get("delta") or "")
                    if delta:
                        assistant_text_parts.append(delta)
                    return
                if event_type in {"response.audio_transcript.done", "response.text.done"}:
                    final_text = str(message.get("transcript") or message.get("text") or "")
                    if final_text and not assistant_text_parts:
                        assistant_text_parts.append(final_text)
                    return
                if event_type == "response.audio.delta":
                    delta = str(message.get("delta") or "")
                    if not delta:
                        return
                    try:
                        audio_pcm = base64.b64decode(delta)
                    except Exception as exc:
                        error_box.append(f"工具前置播报 response.audio.delta base64 解码失败: {exc}")
                        done_event.set()
                        return
                    if audio_pcm and not first_audio_at_ms_box:
                        first_audio_at_ms_box.append(now_ms)
                        log_info(
                            self_logger,
                            (
                                "Omni Realtime 工具前置播报返回首段音频 "
                                f"model={settings.voice_omni_realtime_model_name} "
                                f"voice={settings.voice_model_voice} bytes={len(audio_pcm)} "
                                f"response_create_to_first_audio_ms="
                                f"{DashscopeOmniRealtimeReplyClient._latency_ms(created_at_ms_box[-1] if created_at_ms_box else None, now_ms)}"
                            ),
                            LogContext(device_id=device_id, session_id=session_id, message_id=stream_id),
                        )
                    if audio_pcm:
                        audio_bytes_box.append(len(audio_pcm))
                        on_chunk(ModelChunk(audio_pcm_bytes=audio_pcm, sample_rate_hz=MODEL_OUTPUT_SAMPLE_RATE_HZ))
                    return
                if event_type == "response.audio.done":
                    done_event.set()
                    return
                if event_type in {"response.done", "response.cancelled"}:
                    done_event.set()
                    return
                if event_type == "error":
                    error = message.get("error")
                    error_box.append(str(error if error is not None else message))
                    done_event.set()

        callback = _Callback()
        factory = self._conversation_factory or OmniRealtimeConversation
        conversation = factory(
            model=settings.voice_omni_realtime_model_name,
            callback=callback,
            url=settings.voice_omni_realtime_url.rstrip("/"),
            api_key=settings.dashscope_api_key,
        )
        try:
            conversation.connect()
            conversation.update_session(
                output_modalities=[MultiModality.TEXT, MultiModality.AUDIO],
                voice=settings.voice_model_voice,
                output_audio_format=AudioFormat.PCM_24000HZ_MONO_16BIT,
                instructions="你只负责把系统给出的工具等待提示自然朗读出来，不要添加解释。",
            )
            conversation.create_response(
                instructions=(
                    "请只朗读下面这句中文提示，不要添加任何其他内容："
                    f"{prompt_text}"
                ),
                output_modalities=[MultiModality.TEXT, MultiModality.AUDIO],
            )
            if not done_event.wait(max(5.0, settings.voice_model_timeout_ms / 1000)):
                raise build_error(
                    ErrorCode.TIMEOUT,
                    "Omni Realtime 工具前置播报等待响应完成超时",
                    details={"stream_id": stream_id, "timeout_ms": settings.voice_model_timeout_ms},
                )
            if error_box:
                raise build_error(
                    ErrorCode.INTERNAL_ERROR,
                    "Omni Realtime 工具前置播报返回错误",
                    details={"reason": error_box[-1], "stream_id": stream_id},
                )
            if not audio_bytes_box:
                raise build_error(
                    ErrorCode.INTERNAL_ERROR,
                    "Omni Realtime 工具前置播报没有返回音频",
                    details={"stream_id": stream_id, "text": prompt_text},
                )
            return OmniRealtimeReplyResult(
                assistant_text="".join(assistant_text_parts).strip(),
                transcript="",
                response_id=response_id_box[-1] if response_id_box else None,
            )
        except AppError:
            raise
        except Exception as exc:
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                "Omni Realtime 工具前置播报调用失败",
                details={"reason": str(exc), "stream_id": stream_id},
            ) from exc
        finally:
            try:
                conversation.close()
            except Exception:
                pass

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    @staticmethod
    def _latency_ms(start: int | None, end: int | None) -> int | None:
        if start is None or end is None:
            return None
        return max(end - start, 0)


class DashscopeSpeechRecognitionClient(SpeechRecognitionClient):
    """百炼 ASR 客户端。

    主要功能：
    1. 使用 OpenAI SDK 兼容模式调用百炼 ASR 模型。
    2. 把完整用户音频转成纯文本。
    """

    def __init__(self, sdk_client: Any | None = None) -> None:
        """初始化 ASR 客户端。

        参数：
        1. `sdk_client`：可选的 OpenAI SDK 客户端；测试时可注入假对象。
        """

        self._sdk_client = sdk_client

    def start_streaming_session(
        self,
        *,
        settings: ServerSettings,
        session_id: str,
        device_id: str,
        segment_id: str,
        stream_id: str,
        sample_rate_hz: int,
        channels: int,
        codec: str,
    ) -> StreamingSpeechRecognitionSession | None:
        """启动百炼实时 ASR 会话。

        主要逻辑：
        1. 仅在 `VOICE_ASR_MODE=realtime`、16k 单声道 PCM16 输入下启用。
        2. 返回后台 WebSocket 会话，音频帧会边到达边转发给 ASR 服务。
        3. 初始化失败不阻塞语音主链路，由旧批量 ASR 兜底。
        """

        if settings.voice_asr_mode != "realtime":
            return None
        if channels != 1 or codec.lower() not in {"pcm16", "pcm16le"}:
            return None
        if sample_rate_hz != SERVER_SAMPLE_RATE_HZ:
            return None
        if not settings.dashscope_api_key.strip():
            return None
        try:
            return DashscopeRealtimeSpeechRecognitionSession(
                settings=settings,
                session_id=session_id,
                device_id=device_id,
                segment_id=segment_id,
                stream_id=stream_id,
                sample_rate_hz=sample_rate_hz,
            )
        except Exception as exc:
            log_debug(
                get_logger("server.voice"),
                (
                    "实时 ASR 会话启动失败，回退批量 ASR: "
                    f"segment_id={segment_id} input_stream_id={stream_id} error={exc!r}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )
            return None

    def transcribe(self, *, settings: ServerSettings, input_wav: bytes) -> str:
        """调用百炼 ASR 把语音转写成文本。

        主要逻辑：
        1. 使用独立 ASR 模型 `qwen3-asr-flash`。
        2. 通过 OpenAI SDK 兼容接口提交单轮音频。
        3. 从返回结果中提取转写文本。

        参数：
        1. `settings`：服务端配置。
        2. `input_wav`：完整 WAV 音频。

        返回值：
        1. 转写后的文本；若为空则返回空字符串。

        异常情况：
        1. SDK 调用失败时抛出 `AppError(INTERNAL_ERROR)`。
        """

        if not settings.dashscope_api_key.strip():
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "缺少 DASHSCOPE_API_KEY，无法执行语音转写",
            )

        client = self._sdk_client or DashscopeVoiceModelClient._create_sdk_client(settings)
        try:
            completion = client.chat.completions.create(
                model=getattr(settings, "voice_asr_model_name", "qwen3-asr-flash"),
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_audio",
                                "input_audio": {
                                    "data": build_audio_data_url(input_wav),
                                },
                            },
                        ],
                    }
                ],
                stream=False,
                extra_body={
                    "asr_options": {
                        "enable_itn": False,
                    }
                },
                timeout=settings.voice_model_timeout_ms / 1000,
            )
        except Exception as exc:
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                "百炼 ASR 接口调用失败",
                details={
                    "status": getattr(exc, "status_code", None),
                    "reason": str(exc),
                    "body": DashscopeVoiceModelClient._extract_sdk_error_body(exc),
                },
            ) from exc

        text = extract_message_text(completion)
        return text.strip()


class DashscopeRealtimeSpeechRecognitionSession(StreamingSpeechRecognitionSession):
    """基于百炼 Recognition SDK 的 Fun-ASR 实时转写会话。

    主要功能：
    1. 按官方实时 ASR SDK，在会话启动后持续调用 `send_audio_frame(...)`。
    2. 通过 `RecognitionCallback.on_event(...)` 接收中间识别文本和句尾文本。
    3. 在 `finish()` 中调用 `stop()`，等待服务端返回完整识别结果。

    主要属性：
    1. `_first_audio_chunk_at_ms`：服务端收到并送入 ASR 的首个音频 chunk 时间。
    2. `_first_partial_at_ms`：ASR 服务返回第一段文本的时间。
    3. `_completed_at_ms`：ASR 服务完成本轮实时识别的时间。
    """

    def __init__(
        self,
        *,
        settings: ServerSettings,
        session_id: str,
        device_id: str,
        segment_id: str,
        stream_id: str,
        sample_rate_hz: int,
    ) -> None:
        self._settings = settings
        self._session_id = session_id
        self._device_id = device_id
        self._segment_id = segment_id
        self._stream_id = stream_id
        self._sample_rate_hz = sample_rate_hz
        self._created_at_ms = int(time.time() * 1000)
        self._done_event = threading.Event()
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self._error: BaseException | None = None
        self._final_sentences: list[str] = []
        self._final_text = ""
        self._latest_partial_text = ""
        self._first_audio_chunk_at_ms: int | None = None
        self._first_audio_send_returned_at_ms: int | None = None
        self._first_partial_at_ms: int | None = None
        self._first_sentence_end_at_ms: int | None = None
        self._final_text_at_ms: int | None = None
        self._completed_at_ms: int | None = None
        self._start_called_at_ms: int | None = None
        self._open_at_ms: int | None = None
        self._stop_requested_at_ms: int | None = None
        self._audio_frame_count = 0
        self._audio_bytes_sent = 0
        self._audio_bytes_before_first_partial: int | None = None
        self._dashscope_first_package_delay_ms: int | None = None
        self._dashscope_last_package_delay_ms: int | None = None
        self._logger = get_logger("server.voice")
        self._recognition = self._create_recognition()
        self._start_called_at_ms = int(time.time() * 1000)
        self._recognition.start()

    def _create_recognition(self) -> Any:
        """创建官方 DashScope Recognition 实时 ASR 会话。

        主要逻辑：
        1. 设置 DashScope API Key 和 WebSocket 地址。
        2. 通过 `RecognitionCallback` 接收实时识别事件。
        3. 创建 `Recognition` 对象，输入格式固定为裸 PCM16 音频。

        返回值：
        1. 已配置但尚未 start 的 Recognition 实例。

        异常情况：
        1. dashscope 依赖缺失或 Recognition 初始化失败时向上抛出，由上层回退批量 ASR。
        """

        import dashscope
        from dashscope.audio.asr import Recognition, RecognitionCallback

        session = self
        dashscope.api_key = self._settings.dashscope_api_key
        dashscope.base_websocket_api_url = self._settings.tts_websocket_api_url

        class _Callback(RecognitionCallback):
            """DashScope 实时 ASR 回调桥。"""

            def on_open(self) -> None:  # pragma: no cover - 真实联调路径
                session._open_at_ms = int(time.time() * 1000)
                log_debug(
                    session._logger,
                    (
                        "实时 ASR 连接已建立 "
                        f"model={session._settings.voice_asr_realtime_model_name} "
                        f"segment_id={session._segment_id} input_stream_id={session._stream_id} "
                        f"recognition_open_latency_ms="
                        f"{session._latency_ms(session._created_at_ms, session._open_at_ms)}"
                    ),
                    LogContext(device_id=session._device_id, session_id=session._session_id),
                )

            def on_close(self) -> None:  # pragma: no cover - 真实联调路径
                return None

            def on_complete(self) -> None:
                session._handle_recognition_complete()

            def on_error(self, result: Any) -> None:
                session._handle_recognition_error(result)

            def on_event(self, result: Any) -> None:
                session._handle_recognition_event(result)

        return Recognition(
            model=self._settings.voice_asr_realtime_model_name,
            format="pcm",
            sample_rate=self._sample_rate_hz,
            semantic_punctuation_enabled=False,
            max_sentence_silence=self._settings.voice_asr_realtime_max_sentence_silence_ms,
            callback=_Callback(),
        )

    def append_audio(self, pcm_bytes: bytes) -> None:
        """追加实时 ASR 音频分片。"""

        if not pcm_bytes or self._closed.is_set():
            return
        now_ms = int(time.time() * 1000)
        is_first_audio = False
        with self._lock:
            if self._first_audio_chunk_at_ms is None:
                self._first_audio_chunk_at_ms = now_ms
                is_first_audio = True
            self._audio_frame_count += 1
            self._audio_bytes_sent += len(pcm_bytes)
        try:
            self._recognition.send_audio_frame(bytes(pcm_bytes))
            returned_at_ms = int(time.time() * 1000)
            if is_first_audio:
                self._first_audio_send_returned_at_ms = returned_at_ms
                log_debug(
                    self._logger,
                    (
                        "实时 ASR 首个音频分片已发送 "
                        f"segment_id={self._segment_id} input_stream_id={self._stream_id} "
                        f"bytes={len(pcm_bytes)} frame_count={self._audio_frame_count} "
                        f"session_start_to_first_audio_ms="
                        f"{self._latency_ms(self._created_at_ms, self._first_audio_chunk_at_ms)} "
                        f"send_audio_frame_cost_ms={self._latency_ms(now_ms, returned_at_ms)}"
                    ),
                    LogContext(device_id=self._device_id, session_id=self._session_id),
                )
        except Exception as exc:  # noqa: BLE001 - 真实 ASR SDK 可能抛出多类运行时异常
            self._error = build_error(
                ErrorCode.INTERNAL_ERROR,
                "实时 ASR 音频发送失败",
                details={"segment_id": self._segment_id, "stream_id": self._stream_id, "reason": str(exc)},
            )
            self._done_event.set()

    def finish(self) -> str:
        """结束实时 ASR 音频输入并返回最终文本。"""

        if not self._closed.is_set():
            self._closed.set()
            self._stop_requested_at_ms = int(time.time() * 1000)
            threading.Thread(
                target=self._stop_recognition,
                name=f"asr-stop-{self._device_id}-{self._segment_id}",
                daemon=True,
            ).start()
        completed = self._done_event.wait(timeout=max(self._settings.voice_asr_realtime_timeout_ms / 1000, 0.1))
        if not completed:
            raise build_error(
                ErrorCode.TIMEOUT,
                "实时 ASR 等待最终结果超时",
                retryable=True,
                details={"segment_id": self._segment_id, "stream_id": self._stream_id},
            )
        if self._error is not None:
            if isinstance(self._error, AppError):
                raise self._error
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                "实时 ASR 调用失败",
                details={"segment_id": self._segment_id, "reason": str(self._error)},
            )
        with self._lock:
            if self._completed_at_ms is None:
                self._completed_at_ms = int(time.time() * 1000)
            if not self._final_text and self._latest_partial_text:
                self._final_text = self._latest_partial_text
            return self._final_text.strip()

    def _stop_recognition(self) -> None:
        """在后台停止 DashScope Recognition 会话。

        主要逻辑：
        1. 调用官方 SDK 的 `stop()` 触发 ASR 收尾。
        2. `stop()` 内部会等待服务端完成，不能阻塞语音主链路线程。
        3. 如果停止失败，记录错误并唤醒 `finish()` 走批量 ASR 回退。
        """

        try:
            self._recognition.stop()
        except Exception as exc:  # noqa: BLE001 - stop 失败需要回退批量 ASR
            if self._error is None:
                self._error = build_error(
                    ErrorCode.INTERNAL_ERROR,
                    "实时 ASR 停止失败",
                    details={"segment_id": self._segment_id, "stream_id": self._stream_id, "reason": str(exc)},
                )
            self._done_event.set()

    def metrics(self) -> dict[str, int | None]:
        """返回实时 ASR 首文本和总耗时指标。"""

        total_finished_at_ms = self._completed_at_ms or self._final_text_at_ms
        return {
            "first_audio_chunk_at_ms": self._first_audio_chunk_at_ms,
            "first_asr_partial_latency_ms": self._latency_from_first_audio(self._first_partial_at_ms),
            "asr_total_latency_ms": self._latency_from_first_audio(total_finished_at_ms),
            "recognition_open_latency_ms": self._latency_ms(self._created_at_ms, self._open_at_ms),
            "session_start_to_first_audio_ms": self._latency_ms(self._created_at_ms, self._first_audio_chunk_at_ms),
            "first_audio_send_cost_ms": self._latency_ms(
                self._first_audio_chunk_at_ms,
                self._first_audio_send_returned_at_ms,
            ),
            "stop_to_complete_ms": self._latency_ms(self._stop_requested_at_ms, self._completed_at_ms),
            "audio_ms_before_first_partial": self._audio_duration_ms(self._audio_bytes_before_first_partial),
            "audio_frame_count": self._audio_frame_count,
            "audio_bytes_sent": self._audio_bytes_sent,
            "dashscope_first_package_delay_ms": self._dashscope_first_package_delay_ms,
            "dashscope_last_package_delay_ms": self._dashscope_last_package_delay_ms,
        }

    def _latency_from_first_audio(self, end_at_ms: int | None) -> int | None:
        """按首个音频分片时间计算耗时。"""

        if self._first_audio_chunk_at_ms is None or end_at_ms is None:
            return None
        return max(end_at_ms - self._first_audio_chunk_at_ms, 0)

    @staticmethod
    def _latency_ms(start_at_ms: int | None, end_at_ms: int | None) -> int | None:
        """计算两个毫秒时间戳之间的耗时。"""

        if start_at_ms is None or end_at_ms is None:
            return None
        return max(end_at_ms - start_at_ms, 0)

    def _audio_duration_ms(self, byte_count: int | None) -> int | None:
        """按 PCM16 单声道字节数估算已发送音频时长。"""

        if byte_count is None or self._sample_rate_hz <= 0:
            return None
        return int(byte_count * 1000 / (self._sample_rate_hz * SERVER_SAMPLE_WIDTH_BYTES))

    def _read_recognition_metric(self, method_name: str) -> int | None:
        """读取 DashScope SDK 自带的延迟指标。"""

        getter = getattr(self._recognition, method_name, None)
        if not callable(getter):
            return None
        try:
            value = getter()
        except Exception:
            return None
        if isinstance(value, (int, float)) and value >= 0:
            return int(value)
        return None

    def _handle_recognition_event(self, result: Any) -> None:
        """处理 DashScope Recognition 实时识别事件。"""

        text, is_sentence_end = _extract_recognition_sentence(result)
        if not text:
            return
        now_ms = int(time.time() * 1000)
        should_log_first_partial = False
        with self._lock:
            if self._first_partial_at_ms is None:
                self._first_partial_at_ms = now_ms
                self._audio_bytes_before_first_partial = self._audio_bytes_sent
                self._dashscope_first_package_delay_ms = self._read_recognition_metric("get_first_package_delay")
                should_log_first_partial = True
            if is_sentence_end:
                self._final_sentences.append(text)
                self._final_text = "".join(self._final_sentences)
                self._latest_partial_text = ""
                self._final_text_at_ms = now_ms
                if self._first_sentence_end_at_ms is None:
                    self._first_sentence_end_at_ms = now_ms
            else:
                self._latest_partial_text = text
        if should_log_first_partial:
            first_asr_partial_latency_ms = self._latency_from_first_audio(now_ms)
            audio_ms_before_first_partial = self._audio_duration_ms(self._audio_bytes_before_first_partial)
            log_debug(
                self._logger,
                (
                    "实时 ASR 返回首个文本 "
                    f"first_asr_partial_latency_ms={first_asr_partial_latency_ms} "
                    f"dashscope_first_package_delay_ms={self._dashscope_first_package_delay_ms} "
                    f"audio_ms_before_first_partial={audio_ms_before_first_partial} "
                    f"audio_bytes_before_first_partial={self._audio_bytes_before_first_partial} "
                    f"frame_count={self._audio_frame_count} "
                    f"segment_id={self._segment_id} input_stream_id={self._stream_id} "
                    f"is_sentence_end={is_sentence_end} text_preview={text[:24]!r}"
                ),
                LogContext(device_id=self._device_id, session_id=self._session_id),
            )

    def _handle_recognition_complete(self) -> None:
        """处理 DashScope Recognition 完成事件。"""

        with self._lock:
            if not self._final_text and self._latest_partial_text:
                self._final_text = self._latest_partial_text
            if self._completed_at_ms is None:
                self._completed_at_ms = int(time.time() * 1000)
            self._dashscope_last_package_delay_ms = self._read_recognition_metric("get_last_package_delay")
        self._done_event.set()

    def _handle_recognition_error(self, result: Any) -> None:
        """处理 DashScope Recognition 错误事件。"""

        message = str(read_attr_or_key(result, "message") or result)
        request_id = str(read_attr_or_key(result, "request_id") or "")
        self._error = build_error(
            ErrorCode.INTERNAL_ERROR,
            "实时 ASR 服务返回失败事件",
            details={
                "segment_id": self._segment_id,
                "stream_id": self._stream_id,
                "request_id": request_id,
                "message": message,
            },
        )
        with self._lock:
            if self._completed_at_ms is None:
                self._completed_at_ms = int(time.time() * 1000)
        self._done_event.set()


class PCM16StreamResampler:
    """流式 PCM16 单声道重采样器。"""

    def __init__(self, input_rate_hz: int, output_rate_hz: int) -> None:
        self._input_rate_hz = input_rate_hz
        self._output_rate_hz = output_rate_hz
        self._position = 0.0
        self._carry: list[int] = []

    def push(self, pcm_bytes: bytes, *, final: bool = False) -> bytes:
        if self._input_rate_hz == self._output_rate_hz:
            return pcm_bytes

        sample_count = len(pcm_bytes) // 2
        if sample_count == 0:
            if final:
                self._carry.clear()
                self._position = 0.0
            return b""

        samples = list(self._carry)
        samples.extend(struct.unpack("<" + "h" * sample_count, pcm_bytes[: sample_count * 2]))
        if len(samples) < 2 and not final:
            self._carry = samples
            return b""

        step = self._input_rate_hz / self._output_rate_hz
        max_position = len(samples) - 1 if final else len(samples) - 2
        out_samples: list[int] = []
        while self._position <= max_position:
            index = int(self._position)
            frac = self._position - index
            left = samples[index]
            right = samples[index + 1] if index + 1 < len(samples) else left
            value = int(round(left + (right - left) * frac))
            value = max(-32768, min(32767, value))
            out_samples.append(value)
            self._position += step

        keep_from = max(0, int(math.floor(self._position)) - 1)
        self._carry = samples[keep_from:]
        self._position -= keep_from

        if final and out_samples:
            self._carry.clear()
            self._position = 0.0

        return struct.pack("<" + "h" * len(out_samples), *out_samples) if out_samples else b""


class VoiceRuntime:
    """Phase C 语音主链路运行时。"""

    def __init__(
        self,
        *,
        settings: ServerSettings,
        send_control_message: Callable[[str, str, str, str, dict[str, Any]], None],
        model_client: VoiceModelClient | None = None,
        asr_client: SpeechRecognitionClient | None = None,
        omni_realtime_client: DashscopeOmniRealtimeReplyClient | None = None,
        agent_facade: AgentFacade | None = None,
        realtime_model_adapter: RealtimeModelAdapter | None = None,
    ) -> None:
        self._settings = settings
        self._send_control_message = send_control_message
        self._model_client = model_client or DashscopeVoiceModelClient()
        self._asr_client = asr_client or DashscopeSpeechRecognitionClient()
        self._omni_realtime_client = omni_realtime_client or DashscopeOmniRealtimeReplyClient()
        self._logger = get_logger("server.voice")
        self._lock = threading.Lock()
        self._playback_condition = threading.Condition(self._lock)
        self._controllers: dict[str, VoiceSessionController] = {}
        self._playback_streams: dict[tuple[str, str], PlaybackStreamContext] = {}
        self._notification_stream_requests: dict[tuple[str, str], str] = {}
        self._notification_request_streams: dict[str, tuple[str, str]] = {}
        self._interrupted_playback_streams: set[tuple[str, str]] = set()
        self._progress_audio_cache: dict[str, ProgressAudioCacheEntry] = {}
        self._progress_audio_cache_lock = threading.Lock()
        self._progress_audio_cache_ready = threading.Event()
        self._agent_facade = agent_facade or AgentFacade.build_default(
            settings=settings,
            device_state_reader=self.build_runtime_snapshot,
        )
        self._task_event_bridge = TaskEventBridge(session_store=self._agent_facade.get_session_store())
        self._notification_coordinator = NotificationCoordinator(
            dispatcher=self._dispatch_notification_request,
            interrupter=self._interrupt_notification_request,
        )
        self._playback_arbiter = PlaybackArbiter()
        self._text_dialog_state_machine = TextDialogStateMachine()
        self._realtime_voice_runtime = RealtimeVoiceRuntime(
            playback_arbiter=self._playback_arbiter,
            send_control_message=self._send_control_message,
            settings=self._settings,
            model_adapter=realtime_model_adapter,
        )
        self._start_progress_audio_cache_preload()

    def open_session(self, *, device_id: str, device_type: str, session_id: str) -> None:
        with self._lock:
            controller = self._controllers.get(device_id)
            if controller is None:
                controller = VoiceSessionController(
                    device_id=device_id,
                    device_type=device_type,
                    session_id=session_id,
                )
                self._controllers[device_id] = controller
            else:
                if controller.persistent_omni_realtime_session is not None:
                    controller.persistent_omni_realtime_session.close(blocking=False)
                controller.device_type = device_type
                controller.session_id = session_id
                controller.state = "opened"
                controller.current_segment = None
                controller.current_playback = None
                controller.pending_playbacks.clear()
                controller.last_playback_stream_id = None
                controller.last_playback_state = None
                controller.last_playback_reason = None
                controller.persistent_omni_realtime_session = None

    def build_open_payload(self) -> dict[str, Any]:
        return {
            "sample_rate": SERVER_SAMPLE_RATE_HZ,
            "channels": SERVER_CHANNELS,
            "codec": "pcm16",
            "wake_word": {
                "engine": "esp-sr-wakenet",
                "model": "WakeNet9",
                "enabled": True,
                "max_capture_ms": 10000,
                "pre_roll_ms": 200,
            },
            "endpoint": {
                "trailing_silence_ms": 800,
                "max_capture_ms": 10000,
            },
            "playback": {
                "mode": "stream",
                "format": "pcm16",
                "interrupt_policy": "forbid",
            },
        }

    def _start_progress_audio_cache_preload(self) -> None:
        """启动工具前置播报音频缓存预生成。

        主要逻辑：
        1. 先检查全局工具前置播报开关。
        2. 从当前 agent-core 工具注册表读取所有 `progress_message`。
        3. 离线缓存模式下，服务启动后在后台生成或加载本地 WAV 缓存。
        4. 没有播报文案、文案被删除或文案变化时，同步清理旧缓存，避免下次误用。

        异常情况：
        1. 预生成失败只写 DEBUG 日志，不阻塞服务启动。
        """

        if not self._settings.tool_progress_audio_enabled:
            self._clear_progress_audio_cache_on_startup(reason="disabled")
            return
        progress_provider = self._progress_audio_provider()
        if self._settings.tool_progress_audio_mode != "cached" or progress_provider != "tts":
            self._progress_audio_cache_ready.set()
            log_info(
                self._logger,
                (
                    "工具前置播报音频缓存已跳过 "
                    f"mode={self._settings.tool_progress_audio_mode} provider={progress_provider}"
                ),
                LogContext(session_id="progress_audio_cache", device_id="server"),
            )
            return
        if not self._settings.dashscope_api_key.strip():
            self._progress_audio_cache_ready.set()
            return
        try:
            tool_registry = self._agent_facade.get_tool_registry()
            list_messages = getattr(tool_registry, "list_progress_messages", None)
            if not callable(list_messages):
                self._progress_audio_cache_ready.set()
                return
            progress_messages = list_messages()
        except Exception as exc:
            self._progress_audio_cache_ready.set()
            log_debug(
                self._logger,
                f"工具前置播报缓存读取工具列表失败，已跳过: reason={exc!r}",
                LogContext(session_id="progress_audio_cache", device_id="server"),
            )
            return
        if not progress_messages:
            self._clear_progress_audio_cache_on_startup(reason="no_progress_messages")
            return
        threading.Thread(
            target=self._preload_progress_audio_cache,
            args=(progress_messages,),
            name="progress-audio-cache-preload",
            daemon=True,
        ).start()

    def _clear_progress_audio_cache_on_startup(self, *, reason: str) -> None:
        """启动时把工具前置播报缓存收敛为空。

        主要逻辑：
        1. 当全局关闭或当前工具没有播报文案时，旧的离线缓存已经不再可靠。
        2. 清理缓存目录中的旧 WAV 和元数据，避免后续配置恢复时误读过期提示音。
        3. 无论清理是否成功，都标记预加载结束，避免工具调用链路等待启动任务。

        参数：
        1. `reason`：清理原因，用于 DEBUG/INFO 日志排查启动行为。

        返回值：
        1. 无。

        异常情况：
        1. 缓存目录不存在或无法读取时静默跳过，不阻塞服务启动。
        """

        cache_dir = self._progress_audio_cache_dir()
        self._prune_stale_progress_audio_cache(cache_dir=cache_dir, expected_profiles={})
        with self._progress_audio_cache_lock:
            self._progress_audio_cache.clear()
        self._progress_audio_cache_ready.set()
        log_info(
            self._logger,
            f"工具前置播报音频缓存已收敛为空 reason={reason}",
            LogContext(session_id="progress_audio_cache", device_id="server"),
        )

    def _preload_progress_audio_cache(self, progress_messages: list[tuple[str, str]]) -> None:
        """批量加载或生成工具前置播报音频缓存。"""

        unique_messages: dict[str, str] = {}
        for tool_name, message in progress_messages:
            text = message.strip()
            if text and text not in unique_messages:
                unique_messages[text] = tool_name
        cache_dir = self._progress_audio_cache_dir()
        os.makedirs(cache_dir, exist_ok=True)
        expected_profiles = {
            self._progress_audio_cache_key(text): self._progress_audio_cache_profile(text)
            for text in unique_messages
        }
        self._prune_stale_progress_audio_cache(cache_dir=cache_dir, expected_profiles=expected_profiles)
        succeeded = 0
        for text, tool_name in unique_messages.items():
            try:
                entry = self._load_or_create_progress_audio_cache_entry(
                    tool_name=tool_name,
                    text=text,
                    cache_dir=cache_dir,
                )
            except Exception as exc:  # noqa: BLE001 - 启动预生成失败不应影响主服务
                log_debug(
                    self._logger,
                    f"工具前置播报音频缓存生成失败: tool={tool_name} text={text!r} reason={exc!r}",
                    LogContext(session_id="progress_audio_cache", device_id="server"),
                )
                continue
            with self._progress_audio_cache_lock:
                self._progress_audio_cache[text] = entry
            succeeded += 1
        self._progress_audio_cache_ready.set()
        log_info(
            self._logger,
            (
                "工具前置播报音频缓存预加载完成 "
                f"message_count={len(unique_messages)} cached_count={succeeded} cache_dir={cache_dir}"
            ),
            LogContext(session_id="progress_audio_cache", device_id="server"),
        )

    def _load_or_create_progress_audio_cache_entry(
        self,
        *,
        tool_name: str,
        text: str,
        cache_dir: str,
    ) -> ProgressAudioCacheEntry:
        """加载或创建单条工具前置播报音频缓存。"""

        profile = self._progress_audio_cache_profile(text)
        cache_key = self._progress_audio_cache_key(text)
        wav_path = os.path.join(cache_dir, f"{cache_key}.wav")
        metadata_path = os.path.join(cache_dir, f"{cache_key}.json")
        pcm_bytes = self._read_cached_progress_wav(
            wav_path,
            metadata_path=metadata_path,
            expected_profile=profile,
        )
        if pcm_bytes is None:
            pcm_bytes = self._synthesize_progress_text_to_pcm(text)
            with open(wav_path, "wb") as file:
                file.write(build_wav_bytes(pcm_bytes, SERVER_SAMPLE_RATE_HZ, SERVER_CHANNELS))
            self._write_progress_audio_cache_metadata(metadata_path, profile)
        return ProgressAudioCacheEntry(
            tool_name=tool_name,
            text=text,
            wav_path=wav_path,
            metadata_path=metadata_path,
            profile=profile,
            pcm_bytes=pcm_bytes,
        )

    def _synthesize_progress_text_to_pcm(self, text: str) -> bytes:
        """把一段前置播报文本合成为 16k 单声道 PCM。"""

        pcm_parts: list[bytes] = []
        resampler_box: list[PCM16StreamResampler | None] = [None]

        def _on_chunk(chunk: ModelChunk) -> None:
            if not chunk.audio_pcm_bytes:
                return
            resampler = resampler_box[0]
            if resampler is None or chunk.sample_rate_hz != resampler._input_rate_hz:
                resampler = PCM16StreamResampler(chunk.sample_rate_hz, SERVER_SAMPLE_RATE_HZ)
                resampler_box[0] = resampler
            pcm = resampler.push(chunk.audio_pcm_bytes, final=False)
            if pcm:
                pcm_parts.append(pcm)

        tts_session = self._model_client.create_streaming_tts_session(
            settings=self._settings,
            on_chunk=_on_chunk,
        )
        tts_session.push_text(text)
        tts_session.finish()
        if resampler_box[0] is not None:
            tail = resampler_box[0].push(b"", final=True)
            if tail:
                pcm_parts.append(tail)
        pcm_bytes = b"".join(pcm_parts)
        if not pcm_bytes:
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                "工具前置播报 TTS 返回空音频",
                details={"text": text},
            )
        return pcm_bytes

    def _read_cached_progress_wav(
        self,
        wav_path: str,
        *,
        metadata_path: str,
        expected_profile: dict[str, Any],
    ) -> bytes | None:
        """读取本地缓存 WAV，格式不符合当前播放要求时返回 None。"""

        if not os.path.exists(wav_path):
            return None
        if not self._progress_audio_cache_metadata_matches(metadata_path, expected_profile):
            self._remove_progress_audio_cache_files(wav_path, metadata_path)
            return None
        try:
            with wave.open(wav_path, "rb") as reader:
                if (
                    reader.getframerate() != SERVER_SAMPLE_RATE_HZ
                    or reader.getnchannels() != SERVER_CHANNELS
                    or reader.getsampwidth() != SERVER_SAMPLE_WIDTH_BYTES
                ):
                    self._remove_progress_audio_cache_files(wav_path, metadata_path)
                    return None
                return reader.readframes(reader.getnframes())
        except Exception:
            self._remove_progress_audio_cache_files(wav_path, metadata_path)
            return None

    def _progress_audio_cache_metadata_matches(self, metadata_path: str, expected_profile: dict[str, Any]) -> bool:
        """检查工具前置播报缓存元数据是否与当前模型和音色配置一致。

        主要逻辑：
        1. 元数据必须存在，旧版本没有元数据的 WAV 视为过期。
        2. 元数据中的生成方式、TTS 模型、当前最终播报模型和音色都必须一致。
        3. 任意字段不一致都会触发删除并重新生成。
        """

        try:
            with open(metadata_path, "r", encoding="utf-8") as file:
                metadata = json.load(file)
        except Exception:
            return False
        return metadata == expected_profile

    def _write_progress_audio_cache_metadata(self, metadata_path: str, profile: dict[str, Any]) -> None:
        """写入工具前置播报缓存元数据。"""

        with open(metadata_path, "w", encoding="utf-8") as file:
            json.dump(profile, file, ensure_ascii=False, sort_keys=True, indent=2)

    def _remove_progress_audio_cache_files(self, wav_path: str, metadata_path: str) -> None:
        """删除一组过期或损坏的工具前置播报缓存文件。"""

        for path in (wav_path, metadata_path):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError as exc:
                log_debug(
                    self._logger,
                    f"工具前置播报缓存删除失败，已忽略: path={path} reason={exc!r}",
                    LogContext(session_id="progress_audio_cache", device_id="server"),
                )

    def _prune_stale_progress_audio_cache(
        self,
        *,
        cache_dir: str,
        expected_profiles: dict[str, dict[str, Any]],
    ) -> None:
        """启动时清理与当前播报模型、生成方式或音色不一致的旧缓存。"""

        removed = 0
        try:
            names = os.listdir(cache_dir)
        except OSError:
            return
        basenames = {name.rsplit(".", 1)[0] for name in names if name.endswith((".wav", ".json"))}
        for basename in basenames:
            wav_path = os.path.join(cache_dir, f"{basename}.wav")
            metadata_path = os.path.join(cache_dir, f"{basename}.json")
            expected_profile = expected_profiles.get(basename)
            should_remove = expected_profile is None
            if expected_profile is not None and not self._progress_audio_cache_metadata_matches(
                metadata_path,
                expected_profile,
            ):
                should_remove = True
            if should_remove:
                self._remove_progress_audio_cache_files(wav_path, metadata_path)
                removed += 1
        if removed:
            log_info(
                self._logger,
                f"工具前置播报旧缓存已清理 removed_count={removed} cache_dir={cache_dir}",
                LogContext(session_id="progress_audio_cache", device_id="server"),
            )

    def _progress_audio_cache_dir(self) -> str:
        """返回工具前置播报音频缓存目录。"""

        return os.path.join(self._settings.voice_runs_root, "progress-audio-cache")

    def _progress_audio_cache_key(self, text: str) -> str:
        """按当前前置播报与最终播报配置生成稳定缓存键。"""

        payload = self._progress_audio_cache_profile(text)
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:24]

    def _progress_audio_cache_profile(self, text: str) -> dict[str, Any]:
        """生成工具前置播报缓存指纹。

        主要逻辑：
        1. 记录缓存实际生成方式，目前为专用 TTS。
        2. 同时记录当前最终回复的音频模型和音色，避免切换 Omni voice 后继续复用旧前置播报。
        3. 采样率和播放格式也纳入指纹，防止格式不一致的 WAV 被误用。
        """

        if self._settings.effective_voice_server_mode() == "omni_server":
            reply_audio_provider = "omni_realtime"
            reply_model_name = self._settings.voice_omni_realtime_model_name
            reply_voice = self._settings.voice_model_voice
        else:
            reply_audio_provider = "tts"
            reply_model_name = self._settings.tts_model_name
            reply_voice = self._settings.tts_voice
        return {
            "cache_schema": 2,
            "text": text,
            "tool_progress_audio_mode": self._settings.tool_progress_audio_mode,
            "progress_audio_provider": "tts",
            "tts_model_name": self._settings.tts_model_name,
            "tts_voice": self._settings.tts_voice,
            "tts_sample_rate_hz": self._settings.tts_sample_rate_hz,
            "reply_audio_provider": reply_audio_provider,
            "reply_model_name": reply_model_name,
            "reply_voice": reply_voice,
            "playback_sample_rate_hz": SERVER_SAMPLE_RATE_HZ,
            "channels": SERVER_CHANNELS,
        }

    def _progress_audio_provider(self) -> str:
        """返回工具前置播报应该使用的音频生成方。

        主要逻辑：
        1. 主回复是 Omni Realtime 音频直出时，前置播报也必须使用同一个 Omni Realtime 模型和音色。
        2. 主回复是 Agent 文本加独立 TTS 时，前置播报继续使用同一个 TTS 服务。

        返回值：
            `omni_realtime` 或 `tts`。
        """

        return "omni_realtime" if self._settings.effective_voice_server_mode() == "omni_server" else "tts"

    def build_realtime_open_payload(self) -> dict[str, Any]:
        """生成全双工实时语音会话打开请求。

        返回值：
        1. 端侧可直接识别的 `voice.realtime.session.open` payload。
        """

        return self._realtime_voice_runtime.build_open_payload()

    def open_realtime_session(
        self,
        *,
        device_id: str,
        device_type: str,
        session_id: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """打开 SDK 全双工实时语音会话。"""

        self.open_session(device_id=device_id, device_type=device_type, session_id=session_id)
        return self._realtime_voice_runtime.open_session(
            device_id=device_id,
            device_type=device_type,
            session_id=session_id,
            payload=payload,
        )

    def on_realtime_session_opened(
        self,
        *,
        device_id: str,
        session_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """处理端侧全双工实时语音会话打开确认。"""

        return self._realtime_voice_runtime.on_session_opened(
            device_id=device_id,
            session_id=session_id,
            payload=payload,
        )

    def on_control_connection_closed(self, device_id: str | None) -> None:
        if not device_id:
            return
        with self._lock:
            controller = self._controllers.get(device_id)
            if controller is None:
                return
            if controller.current_segment is not None and controller.current_segment.omni_realtime_session is not None:
                controller.current_segment.omni_realtime_session.close()
            if controller.persistent_omni_realtime_session is not None:
                controller.persistent_omni_realtime_session.close(blocking=False)
                controller.persistent_omni_realtime_session = None
            playback_streams = []
            if controller.current_playback is not None:
                playback_streams.append(controller.current_playback)
            playback_streams.extend(controller.pending_playbacks)
            for playback in playback_streams:
                playback.abort_event.set()
                playback.failed = True
                playback.completed = True
                playback.finished_event.set()
                self._playback_streams.pop((device_id, playback.stream_id), None)
                self._playback_arbiter.remove(device_id=device_id, stream_id=playback.stream_id)
                request_id = self._notification_stream_requests.pop((device_id, playback.stream_id), None)
                if request_id is not None:
                    self._notification_request_streams.pop(request_id, None)
                try:
                    playback.queue.put_nowait(None)
                except queue.Full:
                    pass
            controller.current_playback = None
            controller.pending_playbacks.clear()
            controller.state = "closed"
            controller.audio_connection_peer = None

    def on_voice_session_opened(self, *, device_id: str, session_id: str) -> None:
        controller = self._get_controller(device_id)
        with self._lock:
            self._ensure_session_match(controller, session_id)
            controller.state = "listening"

    def on_segment_started(
        self,
        *,
        device_id: str,
        session_id: str,
        payload: dict[str, Any],
    ) -> None:
        controller = self._get_controller(device_id)
        with self._lock:
            self._ensure_session_match(controller, session_id)
            if controller.current_playback is not None and not controller.current_playback.completed:
                raise build_error(ErrorCode.DEVICE_BUSY, "设备正在播放回复，不能开始新一轮采集")

            stream_id = str(payload.get("stream_id", "")).strip()
            segment_id = str(payload.get("segment_id", "")).strip()
            start_trigger = str(payload.get("trigger") or "").strip()
            if not start_trigger:
                start_trigger = "wake_word" if isinstance(payload.get("wake_word"), dict) else "unknown"
            if not stream_id or not segment_id:
                raise build_error(
                    ErrorCode.INVALID_MESSAGE,
                    "segment.started 缺少 stream_id 或 segment_id",
                    details={"payload": payload},
                )

            segment = SegmentBuffer(
                session_id=session_id,
                stream_id=stream_id,
                segment_id=segment_id,
                sample_rate=int(payload.get("sample_rate", SERVER_SAMPLE_RATE_HZ)),
                channels=int(payload.get("channels", SERVER_CHANNELS)),
                codec=str(payload.get("codec", "pcm16")).strip() or "pcm16",
                started_at_ms=int(time.time() * 1000),
                start_trigger=start_trigger,
            )
            effective_voice_input_mode = self._settings.effective_voice_input_mode()
            if effective_voice_input_mode == "asr_text":
                segment.streaming_asr_session = self._asr_client.start_streaming_session(
                    settings=self._settings,
                    session_id=session_id,
                    device_id=device_id,
                    segment_id=segment.segment_id,
                    stream_id=segment.stream_id,
                    sample_rate_hz=segment.sample_rate,
                    channels=segment.channels,
                    codec=segment.codec,
                )
            controller.current_segment = segment
            controller.state = "receiving_segment"
            should_start_omni_realtime = (
                effective_voice_input_mode == "raw_audio" and self._settings.effective_voice_server_mode() == "omni_server"
            )
            should_start_asr_sidecar = should_start_omni_realtime
        if should_start_omni_realtime:
            self._start_agent_core_omni_realtime_segment_session(
                device_id=device_id,
                session_id=session_id,
                segment=segment,
            )
        if should_start_asr_sidecar:
            self._start_omni_transcript_sidecar(
                device_id=device_id,
                session_id=session_id,
                segment=segment,
            )

    def on_audio_connection_opened(self, *, device_id: str, peer: str) -> None:
        with self._lock:
            controller = self._controllers.get(device_id)
            if controller is None:
                controller = VoiceSessionController(
                    device_id=device_id,
                    device_type="glass",
                    session_id="",
                    state="audio_connected",
                )
                self._controllers[device_id] = controller
            controller.audio_connection_peer = peer

    def on_audio_connection_closed(self, *, device_id: str) -> None:
        with self._lock:
            controller = self._controllers.get(device_id)
            if controller is None:
                return
            controller.audio_connection_peer = None

    def on_audio_frame(self, *, device_id: str, frame: MediaFrame) -> None:
        try:
            if self._realtime_voice_runtime.on_audio_frame(device_id=device_id, frame=frame):
                return
        except AppError as exc:
            log_debug(
                self._logger,
                f"丢弃异常实时语音帧: code={exc.code} message={exc.message}",
                LogContext(device_id=device_id),
            )
            return
        with self._lock:
            controller = self._controllers.get(device_id)
            if controller is None:
                log_debug(
                    self._logger,
                    f"丢弃音频帧：device_id={device_id} 尚未建立控制器",
                    LogContext(device_id=device_id),
                )
                return
            segment = controller.current_segment
            if segment is None:
                log_debug(
                    self._logger,
                    f"丢弃音频帧：device_id={device_id} 当前没有激活中的 segment",
                    LogContext(device_id=device_id, session_id=controller.session_id or None),
                )
                return
            try:
                segment.append_frame(frame, max_bytes=self._settings.max_segment_audio_bytes)
                streaming_asr_session = segment.streaming_asr_session
                sidecar_asr_session = segment.sidecar_asr_session
                omni_realtime_session = segment.omni_realtime_session
            except AppError as exc:
                log_debug(
                    self._logger,
                    f"丢弃异常音频帧: code={exc.code} message={exc.message}",
                    LogContext(device_id=device_id, session_id=controller.session_id or None),
                )
                return
        if streaming_asr_session is not None:
            streaming_asr_session.append_audio(frame.payload)
        if sidecar_asr_session is not None:
            try:
                sidecar_asr_session.append_audio(frame.payload)
            except AppError as exc:
                log_debug(
                    self._logger,
                    f"旁路 ASR 上行音频推送失败: code={exc.code} message={exc.message}",
                    LogContext(device_id=device_id, session_id=controller.session_id or None),
                )
            except Exception as exc:  # noqa: BLE001 - 旁路转写失败不能影响主链路
                log_debug(
                    self._logger,
                    f"旁路 ASR 上行音频推送失败: reason={exc!r}",
                    LogContext(device_id=device_id, session_id=controller.session_id or None),
                )
        if omni_realtime_session is not None:
            try:
                omni_realtime_session.append_audio(frame.payload)
            except AppError as exc:
                log_debug(
                    self._logger,
                    f"Omni Realtime 上行音频推送失败: code={exc.code} message={exc.message}",
                    LogContext(device_id=device_id, session_id=controller.session_id or None),
                )
            except Exception as exc:  # noqa: BLE001 - 预推音频失败时让结束阶段统一降级或报错
                log_debug(
                    self._logger,
                    f"Omni Realtime 上行音频推送失败: reason={exc!r}",
                    LogContext(device_id=device_id, session_id=controller.session_id or None),
                )

    def on_segment_finished(
        self,
        *,
        device_id: str,
        session_id: str,
        payload: dict[str, Any],
    ) -> None:
        controller = self._get_controller(device_id)
        with self._lock:
            self._ensure_session_match(controller, session_id)
            segment = controller.current_segment
            if segment is None:
                raise build_error(
                    ErrorCode.STREAM_NOT_FOUND,
                    "segment.finished 到达时未找到当前音频段",
                    details={"device_id": device_id},
                )
            if payload.get("segment_id") and str(payload.get("segment_id")) != segment.segment_id:
                raise build_error(
                    ErrorCode.INVALID_MESSAGE,
                    "segment.finished.segment_id 与当前段不一致",
                    details={"expected": segment.segment_id, "actual": payload.get("segment_id")},
                )
            controller.current_segment = None
            controller.state = "model_running"

        model_thread = threading.Thread(
            target=self._run_model_pipeline,
            name=f"voice-model-{device_id}",
            daemon=True,
            args=(controller.device_id, controller.session_id, segment),
        )
        model_thread.start()

    def on_playback_started(self, *, device_id: str, session_id: str, stream_id: str) -> None:
        controller = self._get_controller(device_id)
        with self._lock:
            self._ensure_session_match(controller, session_id)
            playback = self._playback_streams.get((device_id, stream_id))
            if playback is None:
                if (device_id, stream_id) in self._interrupted_playback_streams:
                    return
                raise build_error(
                    ErrorCode.STREAM_NOT_FOUND,
                    "actuator.audio.started 对应播放流不存在",
                    details={"device_id": device_id, "stream_id": stream_id},
                )
            playback.started = True
            if controller.current_playback is playback:
                controller.state = "replying"

    def on_playback_finished(self, *, device_id: str, session_id: str, stream_id: str) -> None:
        controller = self._get_controller(device_id)
        next_playback: PlaybackStreamContext | None = None
        notification_request_id: str | None = None
        with self._lock:
            self._ensure_session_match(controller, session_id)
            playback = self._playback_streams.get((device_id, stream_id))
            if playback is None:
                interrupted_key = (device_id, stream_id)
                if interrupted_key in self._interrupted_playback_streams:
                    self._interrupted_playback_streams.discard(interrupted_key)
                    return
                raise build_error(
                    ErrorCode.STREAM_NOT_FOUND,
                    "actuator.audio.finished 对应播放流不存在",
                    details={"device_id": device_id, "stream_id": stream_id},
                )
            if controller.last_playback_stream_id != stream_id:
                controller.last_playback_stream_id = stream_id
                controller.last_playback_state = "completed"
                controller.last_playback_reason = "device_finished"
            playback.completed = True
            playback.finished_event.set()
            self._playback_streams.pop((device_id, stream_id), None)
            next_intent = self._playback_arbiter.complete(device_id=device_id, stream_id=stream_id)
            if controller.current_playback is playback:
                if next_intent is not None:
                    next_playback = self._pop_pending_playback_locked(controller, next_intent.stream_id)
                    if next_playback is not None:
                        controller.current_playback = next_playback
                        controller.state = "reply_streaming"
                    else:
                        controller.current_playback = None
                        controller.state = "listening"
                else:
                    controller.current_playback = None
                    controller.state = "listening"
            elif next_intent is not None and controller.current_playback is None:
                next_playback = self._pop_pending_playback_locked(controller, next_intent.stream_id)
                if next_playback is not None:
                    controller.current_playback = next_playback
                    controller.state = "reply_streaming"
            notification_request_id = self._notification_stream_requests.pop((device_id, stream_id), None)
            if notification_request_id is not None:
                self._notification_request_streams.pop(notification_request_id, None)
        if notification_request_id is not None:
            self._notification_coordinator.complete_request(
                device_id=device_id,
                request_id=notification_request_id,
            )
        if next_playback is not None:
            self._request_playback_start(
                device_id=device_id,
                session_id=session_id,
                playback=next_playback,
                force=not next_playback.queue.empty() or next_playback.completed,
            )
        self._close_continuous_dialog_after_playback_if_needed(
            device_id=device_id,
            session_id=session_id,
            stream_id=stream_id,
        )

    def on_playback_state(
        self,
        *,
        device_id: str,
        session_id: str,
        stream_id: str,
        state: str,
        reason: str | None,
    ) -> None:
        """记录设备上报的结构化播放终态。"""

        controller = self._get_controller(device_id)
        with self._lock:
            self._ensure_session_match(controller, session_id)
            controller.last_playback_stream_id = stream_id
            controller.last_playback_state = state
            controller.last_playback_reason = reason

    def on_realtime_input_started(
        self,
        *,
        device_id: str,
        session_id: str,
        payload: dict[str, Any],
    ) -> None:
        """处理全双工实时语音输入开始事件。"""

        self._realtime_voice_runtime.on_input_started(
            device_id=device_id,
            session_id=session_id,
            payload=payload,
        )

    def on_realtime_input_committed(
        self,
        *,
        device_id: str,
        session_id: str,
        payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """处理全双工实时语音输入提交事件。"""

        return self._realtime_voice_runtime.on_input_committed(
            device_id=device_id,
            session_id=session_id,
            payload=payload,
        )

    def on_realtime_user_interrupt(
        self,
        *,
        device_id: str,
        session_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """处理全双工实时语音用户插话事件。"""

        result = self._realtime_voice_runtime.on_user_interrupt(
            device_id=device_id,
            session_id=session_id,
            payload=payload,
        )
        stream_ids = [
            stream_id
            for stream_id in [
                result.get("interrupted_stream_id"),
                *result.get("dropped_stream_ids", []),
            ]
            if isinstance(stream_id, str) and stream_id
        ]
        if stream_ids:
            self._abort_local_playback_streams_after_realtime_interrupt(
                device_id=device_id,
                stream_ids=stream_ids,
                reason=str(result.get("reason") or "realtime_user_interrupt"),
            )
        return result

    def close_realtime_session(self, *, device_id: str, session_id: str, reason: str = "client_closed") -> None:
        """关闭全双工实时语音会话。"""

        self._realtime_voice_runtime.close_session(device_id=device_id, session_id=session_id, reason=reason)

    def handle_user_interrupt(
        self,
        *,
        device_id: str,
        session_id: str,
        reason: str = "user_voice_interrupt",
        clear_queue: bool = True,
    ) -> dict[str, Any]:
        """处理用户主动打断播放。

        主要逻辑：
        1. 将当前播放和可选待播队列从统一播放仲裁器中移除。
        2. 中断本地播放流队列，并向眼镜下发 `actuator.audio.interrupt`。
        3. 在运行态快照中保留最后一次播放终态和仲裁决策。

        参数：
        1. `device_id`：发生打断的眼镜设备编号。
        2. `session_id`：当前语音会话编号。
        3. `reason`：打断原因，例如 user_voice_interrupt 或 button_interrupt。
        4. `clear_queue`：是否同时清空待播队列。

        返回值：
        1. 可序列化的打断处理结果。

        异常情况：
        1. 设备会话不存在或会话编号不匹配时抛出结构化错误。
        """

        controller = self._get_controller(device_id)
        interrupted_playback: PlaybackStreamContext | None = None
        dropped_playbacks: list[PlaybackStreamContext] = []
        cancelled_notification_request_ids: list[str] = []
        with self._lock:
            self._ensure_session_match(controller, session_id)
            result = self._playback_arbiter.user_interrupt(
                device_id=device_id,
                session_id=session_id,
                reason=reason,
                clear_queue=clear_queue,
            )
            interrupted_playback, notification_request_id = self._remove_playback_by_intent_locked(
                controller=controller,
                intent=result.interrupted_intent,
                reason=reason,
            )
            if notification_request_id is not None:
                cancelled_notification_request_ids.append(notification_request_id)
            for dropped_intent in result.dropped_intents:
                dropped_playback, notification_request_id = self._remove_playback_by_intent_locked(
                    controller=controller,
                    intent=dropped_intent,
                    reason=reason,
                )
                if notification_request_id is not None:
                    cancelled_notification_request_ids.append(notification_request_id)
                if dropped_playback is not None:
                    dropped_playbacks.append(dropped_playback)
            if interrupted_playback is not None:
                controller.last_playback_stream_id = interrupted_playback.stream_id
                controller.last_playback_state = "interrupted"
                controller.last_playback_reason = reason
            if controller.current_playback is None:
                controller.state = "listening"
        for playback in [interrupted_playback, *dropped_playbacks]:
            if playback is None:
                continue
            try:
                playback.queue.put_nowait(None)
            except queue.Full:
                pass
        if interrupted_playback is not None:
            self._send_control_message(
                device_id,
                "request",
                "actuator.audio.interrupt",
                session_id,
                {
                    "device_id": device_id,
                    "stream_id": interrupted_playback.stream_id,
                    "reason": reason,
                    "clear_queue": clear_queue,
                },
            )
        for request_id in cancelled_notification_request_ids:
            self._notification_coordinator.cancel_request(
                device_id=device_id,
                request_id=request_id,
                reason=reason,
                clear_queue=clear_queue,
            )
        return {
            "decision": result.decision.to_dict(),
            "interrupted_stream_id": interrupted_playback.stream_id if interrupted_playback else None,
            "dropped_stream_ids": [playback.stream_id for playback in dropped_playbacks],
            "cancelled_notification_request_ids": cancelled_notification_request_ids,
        }

    def on_task_event(self, event: TaskEvent) -> None:
        """处理后台任务事件。

        主要逻辑：
        1. 通过 `TaskEventBridge` 把事件写入会话上下文。
        2. 对允许直发的事件，交给统一通知协调器裁决与下发。
        3. 对要求回流决策的事件，再转换成 `AgentTurn` 交给 `agent-core`。
        """

        request = self._task_event_bridge.handle_event(event)
        if request is None:
            dispatched = False
        else:
            submit_result = self._notification_coordinator.submit(request)
            dispatched = submit_result.dispatched
        if event.requires_agent_decision:
            threading.Thread(
                target=self._run_task_event_agent_turn,
                args=(event, dispatched),
                daemon=True,
            ).start()

    def submit_notification(
        self,
        *,
        request_id: str,
        source_module: str,
        session_id: str,
        device_id: str,
        task_id: str | None,
        text: str,
        priority: str = "normal",
        notification_type: str = "sdk.notification",
        allow_interrupt: bool | None = None,
        allow_merge: bool | None = None,
        requires_agent_context_sync: bool = False,
        dedupe_key: str | None = None,
    ) -> dict[str, Any]:
        """提交一条外部结构化通知到统一语音播报链路。

        功能：
        1. 供 `DeviceGroupRuntime.notification_adapter` 和后续 SDK 模块复用。
        2. 统一进入 `NotificationCoordinator`、`PlaybackArbiter` 和 TTS/Omni 播放链路。
        3. 避免业务侧通知只进入设备组内存记录，却没有真实下发到眼镜端。

        参数：
        1. `request_id`：通知请求编号。
        2. `source_module`：通知来源模块。
        3. `session_id/device_id/task_id`：通知归属上下文。
        4. `text`：需要播报的文本。
        5. `priority`：通知优先级。
        6. `notification_type`：通知类型。
        7. `allow_interrupt/allow_merge/requires_agent_context_sync/dedupe_key`：通知仲裁策略。

        返回值：
        1. 提交结果字典，包含是否接受、是否已直发、是否排队和原因。

        异常情况：
        1. 空文本不会抛异常，会返回 `accepted=false`。
        """

        resolved_text = text.strip()
        if not resolved_text:
            return {
                "accepted": False,
                "dispatched": False,
                "queued": False,
                "reason": "empty_notification_text",
            }
        resolved_allow_interrupt = priority in {"high", "critical"} if allow_interrupt is None else allow_interrupt
        resolved_allow_merge = priority in {"low", "normal"} if allow_merge is None else allow_merge
        result = self._notification_coordinator.submit(
            NotificationRequest(
                request_id=request_id,
                source_module=source_module,
                session_id=session_id,
                device_id=device_id,
                task_id=task_id,
                priority=priority,
                notification_type=notification_type,
                delivery_mode="audio",
                allow_interrupt=resolved_allow_interrupt,
                allow_merge=resolved_allow_merge,
                requires_agent_context_sync=requires_agent_context_sync,
                dedupe_key=dedupe_key or f"{notification_type}:{task_id or request_id}:{resolved_text}",
                payload={"text": resolved_text},
            )
        )
        return {
            "accepted": result.accepted,
            "dispatched": result.dispatched,
            "queued": result.queued,
            "interrupted_active": result.interrupted_active,
            "reason": result.reason,
            "active_request_id": result.active_request_id,
            "queued_position": result.queued_position,
        }

    def stream_playback(self, handler, *, device_id: str, stream_id: str) -> None:
        playback = self._wait_for_playback(device_id=device_id, stream_id=stream_id, timeout_s=10.0)
        try:
            self._send_chunked_headers(handler)
            self._write_chunk(handler, wav_header_unknown_size(playback.sample_rate, playback.channels))
            log_debug(
                self._logger,
                f"播放流 HTTP 已建立 stream_id={stream_id} sample_rate={playback.sample_rate} channels={playback.channels}",
                LogContext(device_id=device_id, session_id=playback.session_id, message_id=stream_id),
            )

            while True:
                if playback.abort_event.is_set():
                    break
                try:
                    item = playback.queue.get(timeout=0.5)
                except queue.Empty:
                    if playback.completed:
                        break
                    continue
                if item is None:
                    break
                if playback.first_http_audio_chunk_at_ms is None:
                    playback.first_http_audio_chunk_at_ms = self._now_ms()
                    log_info(
                        self._logger,
                        (
                            "播放流写出首段音频 "
                            f"stream_id={stream_id} audio_source={playback.audio_source} bytes={len(item)} "
                            f"play_request_to_http_audio_ms={self._latency_ms(start=playback.first_play_request_at_ms, end=playback.first_http_audio_chunk_at_ms)} "
                            f"source_audio_to_http_audio_ms={self._latency_ms(start=playback.first_audio_chunk_at_ms, end=playback.first_http_audio_chunk_at_ms)}"
                        ),
                        LogContext(device_id=device_id, session_id=playback.session_id, message_id=stream_id),
                    )
                self._write_chunk(handler, item)

            self._finish_chunked(handler)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as exc:
            log_debug(
                self._logger,
                f"播放流 HTTP 客户端已断开: device_id={device_id} stream_id={stream_id} reason={exc.__class__.__name__}",
                LogContext(device_id=device_id, session_id=playback.session_id, message_id=stream_id),
            )

    def build_runtime_snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            controllers = list(self._controllers.values())
        notification_snapshot = self._notification_coordinator.build_snapshot()
        playback_snapshot = self._playback_arbiter.build_snapshot()
        realtime_snapshot = self._realtime_voice_runtime.build_snapshot()
        result: dict[str, dict[str, Any]] = {}
        for controller in controllers:
            current_playback = controller.current_playback
            realtime_device_snapshot = realtime_snapshot.get(controller.device_id, {})
            result[controller.device_id] = {
                "session_id": controller.session_id,
                "state": controller.state,
                "voice_server_mode": self._settings.effective_voice_server_mode(),
                "active_segment_id": controller.current_segment.segment_id if controller.current_segment else None,
                "omni_session_lifecycle": self._settings.voice_omni_session_lifecycle,
                "omni_persistent_connected": controller.persistent_omni_realtime_session is not None,
                "reply_stream_id": current_playback.stream_id if current_playback else None,
                "reply_audio_source": current_playback.audio_source if current_playback else None,
                "reply_first_text_delta_at_ms": current_playback.first_text_delta_at_ms if current_playback else None,
                "reply_first_audio_chunk_at_ms": current_playback.first_audio_chunk_at_ms if current_playback else None,
                "reply_first_play_request_at_ms": current_playback.first_play_request_at_ms if current_playback else None,
                "reply_first_http_audio_chunk_at_ms": current_playback.first_http_audio_chunk_at_ms if current_playback else None,
                "reply_text_to_first_audio_ms": self._latency_ms(
                    start=current_playback.first_text_delta_at_ms if current_playback else None,
                    end=current_playback.first_audio_chunk_at_ms if current_playback else None,
                ),
                "reply_audio_to_play_request_ms": self._latency_ms(
                    start=current_playback.first_audio_chunk_at_ms if current_playback else None,
                    end=current_playback.first_play_request_at_ms if current_playback else None,
                ),
                "reply_play_request_to_http_audio_ms": self._latency_ms(
                    start=current_playback.first_play_request_at_ms if current_playback else None,
                    end=current_playback.first_http_audio_chunk_at_ms if current_playback else None,
                ),
                "audio_connection_online": controller.audio_connection_peer is not None,
                "last_playback_stream_id": controller.last_playback_stream_id,
                "last_playback_state": controller.last_playback_state,
                "last_playback_reason": controller.last_playback_reason,
                "active_playback_intent": playback_snapshot["active_intents"].get(controller.device_id),
                "pending_playback_intents": playback_snapshot["pending_intents"].get(controller.device_id, []),
                "recent_playback_decisions": [
                    decision
                    for decision in playback_snapshot["recent_decisions"]
                    if decision.get("device_id") == controller.device_id
                ],
                "active_notification": notification_snapshot["active_requests"].get(controller.device_id),
                "pending_notifications": notification_snapshot["pending_requests"].get(controller.device_id, []),
                "recent_notification_decisions": [
                    decision
                    for decision in notification_snapshot["recent_decisions"]
                    if decision.get("device_id") == controller.device_id
                ],
                "active_realtime_session": realtime_device_snapshot or None,
                "realtime_state": realtime_device_snapshot.get("realtime_state"),
                "active_realtime_input_stream_id": realtime_device_snapshot.get("active_input_stream_id"),
                "active_realtime_output_stream_id": realtime_device_snapshot.get("active_output_stream_id"),
                "recent_realtime_events": realtime_device_snapshot.get("recent_realtime_events", []),
                "recent_realtime_interrupts": realtime_device_snapshot.get("recent_interrupts", []),
                "realtime_latency_metrics": realtime_device_snapshot.get("latency_metrics", {}),
                "realtime_barge_in_count": realtime_device_snapshot.get("barge_in_count", 0),
                "realtime_echo_rejected_count": realtime_device_snapshot.get("echo_rejected_count", 0),
            }
        return result

    @staticmethod
    def _latency_ms(*, start: int | None, end: int | None) -> int | None:
        """计算两个毫秒时间戳之间的延迟。"""

        if start is None or end is None:
            return None
        return max(0, end - start)

    @property
    def agent_facade(self) -> AgentFacade:
        """返回内部 agent facade，供调试接口使用。"""

        return self._agent_facade

    def _transcribe_segment(
        self,
        *,
        device_id: str,
        session_id: str,
        segment: SegmentBuffer,
        input_wav: bytes,
    ) -> str:
        """获取当前音频段的 ASR 文本。

        主要逻辑：
        1. 如果该段已经建立实时 ASR 会话，优先等待实时 ASR 最终文本。
        2. 实时 ASR 失败、超时或返回空文本时，回退旧的整段 WAV ASR。
        3. 兜底路径保留，避免实时服务异常直接中断语音链路。

        参数：
        1. `device_id/session_id`：用于日志定位。
        2. `segment`：当前音频段。
        3. `input_wav`：旧批量 ASR 兜底需要的完整 WAV。

        返回值：
        1. ASR 转写文本。
        """

        if segment.streaming_asr_session is not None:
            try:
                realtime_text = segment.streaming_asr_session.finish().strip()
                asr_metrics = segment.streaming_asr_session.metrics()
                if realtime_text:
                    log_info(
                        self._logger,
                        (
                            "实时 ASR 完成 "
                            f"segment_id={segment.segment_id} input_stream_id={segment.stream_id} "
                            f"text_length={len(realtime_text)} "
                            f"first_asr_partial_latency_ms={asr_metrics.get('first_asr_partial_latency_ms')} "
                            f"asr_total_latency_ms={asr_metrics.get('asr_total_latency_ms')} "
                            f"recognition_open_latency_ms={asr_metrics.get('recognition_open_latency_ms')} "
                            f"session_start_to_first_audio_ms={asr_metrics.get('session_start_to_first_audio_ms')} "
                            f"first_audio_send_cost_ms={asr_metrics.get('first_audio_send_cost_ms')} "
                            f"audio_ms_before_first_partial={asr_metrics.get('audio_ms_before_first_partial')} "
                            f"dashscope_first_package_delay_ms={asr_metrics.get('dashscope_first_package_delay_ms')} "
                            f"dashscope_last_package_delay_ms={asr_metrics.get('dashscope_last_package_delay_ms')} "
                            f"stop_to_complete_ms={asr_metrics.get('stop_to_complete_ms')} "
                            f"audio_frame_count={asr_metrics.get('audio_frame_count')} "
                            f"audio_bytes_sent={asr_metrics.get('audio_bytes_sent')}"
                        ),
                        LogContext(device_id=device_id, session_id=session_id),
                    )
                    return realtime_text
                log_debug(
                    self._logger,
                    (
                        "实时 ASR 返回空文本，回退批量 ASR "
                        f"segment_id={segment.segment_id} input_stream_id={segment.stream_id}"
                    ),
                    LogContext(device_id=device_id, session_id=session_id),
                )
            except AppError as exc:
                log_debug(
                    self._logger,
                    (
                        "实时 ASR 失败，回退批量 ASR "
                        f"code={exc.code} message={exc.message} details={exc.details} "
                        f"segment_id={segment.segment_id} input_stream_id={segment.stream_id}"
                    ),
                    LogContext(device_id=device_id, session_id=session_id),
                )

        return self._asr_client.transcribe(settings=self._settings, input_wav=input_wav)

    def _start_omni_transcript_sidecar(
        self,
        *,
        device_id: str,
        session_id: str,
        segment: SegmentBuffer,
    ) -> None:
        """启动 Omni 音频直出链路的旁路 ASR。

        主要逻辑：
        1. 根据 ASR 客户端能力尝试启动实时转写会话。
        2. 建连期间已经缓存到本地的 PCM 会按顺序补推给 ASR。
        3. 失败时只记录 DEBUG，段结束后再尝试批量 ASR 旁路转写。

        参数：
            device_id/session_id: 当前设备和会话编号。
            segment: 当前正在接收的语音段。

        返回值：
            无。

        异常情况：
            旁路 ASR 不参与回答主链路，所有异常都会降级为日志。
        """

        try:
            session = self._asr_client.start_streaming_session(
                settings=self._settings,
                session_id=session_id,
                device_id=device_id,
                segment_id=segment.segment_id,
                stream_id=segment.stream_id,
                sample_rate_hz=segment.sample_rate,
                channels=segment.channels,
                codec=segment.codec,
            )
        except Exception as exc:  # noqa: BLE001 - 旁路 ASR 不能影响 Omni 主链路
            segment.sidecar_transcript_error = repr(exc)
            log_debug(
                self._logger,
                (
                    "旁路 ASR 实时会话启动失败，将在语音结束后尝试批量旁路转写 "
                    f"segment_id={segment.segment_id} reason={exc!r}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )
            return
        if session is None:
            log_debug(
                self._logger,
                (
                    "旁路 ASR 实时会话未启用，将在语音结束后尝试批量旁路转写 "
                    f"segment_id={segment.segment_id} input_stream_id={segment.stream_id}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )
            return

        sent_bytes = 0
        try:
            while True:
                with self._lock:
                    controller = self._controllers.get(device_id)
                    if controller is None or controller.current_segment is not segment:
                        return
                    pending = bytes(segment.payload[sent_bytes:])
                    if not pending:
                        segment.sidecar_asr_session = session
                        segment.sidecar_transcript_source = "sidecar_realtime_asr"
                        break
                session.append_audio(pending)
                sent_bytes += len(pending)
            log_debug(
                self._logger,
                (
                    "旁路 ASR 实时输入流已启动 "
                    f"segment_id={segment.segment_id} input_stream_id={segment.stream_id} "
                    f"buffered_audio_bytes={sent_bytes}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )
        except Exception as exc:  # noqa: BLE001 - 旁路 ASR 不能影响 Omni 主链路
            segment.sidecar_transcript_error = repr(exc)
            with self._lock:
                if segment.sidecar_asr_session is session:
                    segment.sidecar_asr_session = None
                    segment.sidecar_transcript_source = ""
            log_debug(
                self._logger,
                (
                    "旁路 ASR 预推缓存失败，将在语音结束后尝试批量旁路转写 "
                    f"segment_id={segment.segment_id} reason={exc!r}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )

    def _finish_sidecar_transcript_async(
        self,
        *,
        device_id: str,
        session_id: str,
        segment: SegmentBuffer,
        input_wav: bytes,
    ) -> None:
        """异步完成旁路 ASR 转写。

        主要逻辑：
        1. 如果已经有实时 ASR 会话，则在后台调用 `finish()` 得到最终文本。
        2. 如果没有实时 ASR 会话，则在后台用完整 WAV 做批量 ASR 兜底。
        3. 结果只用于日志、会话回写和后续上下文，不阻塞 Omni 音频直出。

        参数：
            device_id/session_id: 当前设备和会话编号。
            segment: 当前已结束语音段。
            input_wav: 当前段完整 WAV，供批量 ASR 兜底使用。

        返回值：
            无。
        """

        if segment.sidecar_transcript_done.is_set():
            return

        def _worker() -> None:
            source = segment.sidecar_transcript_source or "sidecar_batch_asr"
            try:
                if segment.sidecar_asr_session is not None:
                    text = segment.sidecar_asr_session.finish().strip()
                    metrics = segment.sidecar_asr_session.metrics()
                    source = "sidecar_realtime_asr"
                else:
                    text = self._asr_client.transcribe(settings=self._settings, input_wav=input_wav).strip()
                    metrics = {}
                    source = "sidecar_batch_asr"
                segment.sidecar_transcript_text = text
                segment.sidecar_transcript_source = source if text else f"{source}_empty"
                segment.sidecar_asr_metrics = metrics
                if text:
                    log_info(
                        self._logger,
                        (
                            "旁路 ASR 转写完成 "
                            f"segment_id={segment.segment_id} input_stream_id={segment.stream_id} "
                            f"source={segment.sidecar_transcript_source} text={_format_log_text(text)!r} "
                            f"asr_total_latency_ms={metrics.get('asr_total_latency_ms') if metrics else None} "
                            f"audio_frame_count={metrics.get('audio_frame_count') if metrics else None}"
                        ),
                        LogContext(device_id=device_id, session_id=session_id),
                    )
            except Exception as exc:  # noqa: BLE001 - 旁路转写不能影响主链路
                segment.sidecar_transcript_error = repr(exc)
                segment.sidecar_transcript_source = f"{source}_failed"
                log_debug(
                    self._logger,
                    (
                        "旁路 ASR 转写失败 "
                        f"segment_id={segment.segment_id} input_stream_id={segment.stream_id} "
                        f"source={source} reason={exc!r}"
                    ),
                    LogContext(device_id=device_id, session_id=session_id),
                )
            finally:
                segment.sidecar_transcript_done.set()

        threading.Thread(
            target=_worker,
            name=f"sidecar-asr-{segment.segment_id}",
            daemon=True,
        ).start()

    def _select_transcript_for_agent(
        self,
        *,
        segment: SegmentBuffer,
        omni_transcript: str,
    ) -> tuple[str, str]:
        """选择写入 Agent-Core 的用户文本。

        主要逻辑：
        1. 旁路 ASR 已完成且有文本时优先使用旁路 ASR。
        2. 旁路 ASR 尚未完成或为空时，临时使用 Omni 返回的转写文本。
        3. 两者都不可用时返回空文本和 `unavailable` 来源。
        """

        sidecar_text = segment.sidecar_transcript_text.strip()
        if segment.sidecar_transcript_done.is_set() and sidecar_text:
            return sidecar_text, segment.sidecar_transcript_source or "sidecar_asr"
        fallback_text = omni_transcript.strip()
        if fallback_text:
            return fallback_text, "omni_fallback"
        return "", "unavailable"

    def _discard_utterance_photo(
        self,
        *,
        device_id: str,
        session_id: str,
        segment: SegmentBuffer,
    ) -> None:
        """丢弃当前语音段关联的自动抓拍。

        主要逻辑：
        1. 从自动照片缓存中找到当前 `segment_id` 对应的记录。
        2. 将记录标记为已消费，避免空语音轮次的图片进入下一次真实对话。
        3. 缓存不存在或丢弃失败时仅写 DEBUG 日志，不影响语音状态恢复。

        参数：
            device_id/session_id: 当前设备和会话编号。
            segment: 需要丢弃自动照片的语音段。

        返回值：
            无。
        """

        try:
            store = self._agent_facade.get_tool_registry().get_utterance_photo_store()
            discarded = store.discard_photo(
                session_id=session_id,
                device_id=device_id,
                segment_id=segment.segment_id,
            )
            if discarded:
                log_debug(
                    self._logger,
                    (
                        "已丢弃空语音段自动抓拍 "
                        f"segment_id={segment.segment_id} input_stream_id={segment.stream_id}"
                    ),
                    LogContext(device_id=device_id, session_id=session_id),
                )
        except Exception as exc:  # noqa: BLE001 - 丢弃照片失败不应阻断语音状态恢复
            log_debug(
                self._logger,
                (
                    "丢弃空语音段自动抓拍失败 "
                    f"segment_id={segment.segment_id} input_stream_id={segment.stream_id} reason={exc!r}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )

    def _should_suppress_empty_continuous_segment(
        self,
        *,
        device_id: str,
        session_id: str,
        segment: SegmentBuffer,
    ) -> bool:
        """判断连续 VAD 触发的空语音段是否应在进模型前丢弃。

        主要逻辑：
        1. 只处理端侧明确标记为 `continuous_vad` 的免唤醒后续段。
        2. 等待旁路 ASR 给出最终结果；如果结果仍为空，认为这轮不是用户真实语音。
        3. 丢弃本轮自动抓拍并关闭预连接 Omni 会话，避免触发“空语音 + 图片”的自循环。

        参数：
            device_id/session_id: 当前设备和会话编号。
            segment: 当前已结束的语音段。

        返回值：
            需要抑制时返回 `True`。
        """

        if segment.start_trigger != "continuous_vad":
            return False
        if segment.sidecar_transcript_done.wait(1.5):
            transcript = segment.sidecar_transcript_text.strip()
            if transcript:
                return False
        self._close_segment_without_reply(
            device_id=device_id,
            session_id=session_id,
            segment=segment,
            reason="empty_continuous_vad",
            close_continuous_dialog=True,
        )
        log_info(
            self._logger,
            (
                "已抑制连续 VAD 空语音段 "
                f"segment_id={segment.segment_id} input_stream_id={segment.stream_id} "
                f"duration_ms={segment.duration_ms()} audio_bytes={len(segment.payload)}"
            ),
            LogContext(device_id=device_id, session_id=session_id),
        )
        return True

    def _is_conversation_stop_command(self, text: str) -> bool:
        """判断用户文本是否是连续对话控制指令。

        主要逻辑：
        1. 去除常见标点和空白，降低 ASR 断句差异影响。
        2. 只匹配短句，避免把包含这些词的正常长问题误判为控制指令。
        3. 命中后由运行时关闭连续对话窗口，不再进入 Agent。
        """

        return self._text_dialog_state_machine.is_stop_command(text)

    def _normalize_voice_intent_text(self, text: str) -> str:
        """归一化语音意图文本。

        主要逻辑：
        1. 去掉空白和常见标点。
        2. 保留中文和普通字符顺序，便于做确定性关键词判断。

        参数：
            text: ASR 或 Omni 返回的原始转写文本。

        返回值：
            可用于意图规则匹配的短文本。
        """

        return self._text_dialog_state_machine.normalize(text)

    def _is_likely_assistant_echo(self, controller: VoiceSessionController, text: str) -> bool:
        """判断连续 VAD 文本是否像上一轮助手播报回声。

        主要逻辑：
        1. 只检查最近几条助手消息。
        2. 如果短文本完整出现在助手回复里，认为更可能是扬声器回灌，而不是用户新意图。

        参数：
            controller: 当前设备语音控制器。
            text: 本轮 ASR 文本。

        返回值：
            True 表示应按噪声/回声忽略。
        """

        return self._text_dialog_state_machine.is_assistant_echo(
            text=text,
            recent_assistant_texts=[entry.text for entry in controller.message_context if entry.role == "assistant"],
        )

    def _decide_voice_turn_intent(
        self,
        *,
        controller: VoiceSessionController,
        segment: SegmentBuffer,
        transcript: str,
    ) -> VoiceTurnIntentDecision:
        """裁决一轮语音是否进入 Agent、是否需要照片。

        主要逻辑：
        1. 停止指令优先，直接关闭连续对话窗口。
        2. 连续 VAD 的空文本、语气词和疑似助手回声按噪声忽略，并关闭连续窗口。
        3. 其他文本作为普通语音进入 Agent；是否需要照片由模型通过 `capture_photo` 工具决定。

        参数：
            controller: 当前设备语音控制器。
            segment: 当前语音段。
            transcript: 旁路 ASR 或 Omni 选出的用户文本。

        返回值：
            `VoiceTurnIntentDecision`，供后续管线控制拍照、回复和会话窗口。
        """

        text_decision = self._text_dialog_state_machine.decide(
            transcript=transcript,
            start_trigger=segment.start_trigger,
            recent_assistant_texts=[entry.text for entry in controller.message_context if entry.role == "assistant"],
        )
        return VoiceTurnIntentDecision(
            intent=text_decision.intent,
            reason=text_decision.reason,
            close_continuous_dialog=text_decision.close_continuous_dialog,
        )

    def _close_segment_without_reply(
        self,
        *,
        device_id: str,
        session_id: str,
        segment: SegmentBuffer,
        reason: str,
        close_continuous_dialog: bool,
    ) -> None:
        """结束当前语音段且不进入模型回复。

        主要逻辑：
        1. 关闭可能已经预连接的 Omni 会话。
        2. 丢弃本轮自动照片，避免后续轮次误消费。
        3. 可选下发 `voice.dialog.close`，让眼镜回到必须唤醒词触发的待命状态。

        参数：
            device_id/session_id: 当前设备和语音会话编号。
            segment: 当前语音段。
            reason: 本轮被忽略或关闭的原因。
            close_continuous_dialog: 是否关闭端侧连续对话窗口。
        """

        if segment.omni_realtime_session is not None:
            try:
                try:
                    segment.omni_realtime_session.close(blocking=False)
                except TypeError:
                    segment.omni_realtime_session.close()
            except Exception as exc:  # noqa: BLE001 - 清理失败只记录
                log_debug(
                    self._logger,
                    f"关闭忽略语音段 Omni 会话失败: segment_id={segment.segment_id} reason={exc!r}",
                    LogContext(device_id=device_id, session_id=session_id),
                )
            if close_continuous_dialog:
                with self._lock:
                    controller = self._controllers.get(device_id)
                    if (
                        controller is not None
                        and controller.persistent_omni_realtime_session is segment.omni_realtime_session
                    ):
                        controller.persistent_omni_realtime_session = None
        if segment.turn_intent == "unknown":
            segment.turn_intent = "ignore"
            segment.turn_intent_reason = reason
        self._discard_utterance_photo(device_id=device_id, session_id=session_id, segment=segment)
        if close_continuous_dialog:
            self._send_control_message(
                device_id,
                "request",
                "voice.dialog.close",
                session_id,
                {
                    "device_id": device_id,
                    "reason": reason,
                    "source": "voice_turn_intent",
                },
            )
        log_info(
            self._logger,
            (
                "语音段已由系统意图裁决忽略 "
                f"segment_id={segment.segment_id} input_stream_id={segment.stream_id} "
                f"reason={reason} close_continuous_dialog={close_continuous_dialog}"
            ),
            LogContext(device_id=device_id, session_id=session_id),
        )

    def _prepare_utterance_photo_for_intent(
        self,
        *,
        device_id: str,
        session_id: str,
        segment: SegmentBuffer,
        decision: VoiceTurnIntentDecision,
    ) -> None:
        """按意图裁决准备自动照片。

        主要逻辑：
        1. 当前主链路不再通过系统前置意图触发照片。
        2. 是否需要照片统一交给模型通过 `capture_photo` 工具决定。
        3. 非工具路径上的旧自动照片记录会被丢弃，避免误进入后续 turn。

        参数：
            device_id/session_id: 当前设备和语音会话编号。
            segment: 当前语音段。
            decision: 系统层意图裁决结果。
        """

        segment.turn_intent = decision.intent
        segment.turn_intent_reason = decision.reason
        self._discard_utterance_photo(device_id=device_id, session_id=session_id, segment=segment)

    def _decide_raw_audio_turn_intent_before_omni(
        self,
        *,
        controller: VoiceSessionController,
        segment: SegmentBuffer,
    ) -> VoiceTurnIntentDecision:
        """在提交 Omni 前完成系统层意图裁决。

        主要逻辑：
        1. 优先等待旁路 ASR 给出最终文本，用文本判断停止、忽略和普通语音。
        2. 如果短语音段在等待窗口内仍没有 ASR 结果，认为更可能是噪声或回声，直接忽略。
        3. 较长语音段 ASR 仍未完成时允许进入 Omni，避免误杀真实长问题。

        参数：
            controller: 当前设备语音控制器。
            segment: 当前待提交 Omni 的音频段。

        返回值：
            本轮系统层意图裁决结果。
        """

        if segment.sidecar_transcript_done.wait(VOICE_TURN_INTENT_SIDECAR_WAIT_SECONDS):
            transcript = segment.sidecar_transcript_text.strip()
            return self._decide_voice_turn_intent(controller=controller, segment=segment, transcript=transcript)
        if segment.duration_ms() <= VOICE_TURN_SHORT_PENDING_ASR_MAX_MS:
            return VoiceTurnIntentDecision(
                intent="ignore",
                reason="short_segment_without_asr",
                close_continuous_dialog=True,
            )
        return VoiceTurnIntentDecision(intent="voice_query", reason="sidecar_asr_pending")

    def _decide_ready_sidecar_intent_before_omni(
        self,
        *,
        controller: VoiceSessionController,
        segment: SegmentBuffer,
    ) -> VoiceTurnIntentDecision:
        """仅在旁路 ASR 已经就绪时做非阻塞系统裁决。

        主要逻辑：
        1. 不等待旁路 ASR，避免在模型调用前增加固定尾延迟。
        2. 已经就绪时只处理两类低风险控制：停止指令、助手回声。
        3. 空文本、语气词和背景音不再由 ASR 前置裁决承担，交给 Omni semantic_vad。

        参数：
            controller: 当前设备语音控制器。
            segment: 当前待进入 Omni 的语音段。

        返回值：
            非阻塞裁决结果；默认放行为 `voice_query`。
        """

        if not segment.sidecar_transcript_done.is_set():
            return VoiceTurnIntentDecision(intent="voice_query", reason="sidecar_asr_not_ready")
        transcript = segment.sidecar_transcript_text.strip()
        if not transcript:
            return VoiceTurnIntentDecision(intent="voice_query", reason="sidecar_asr_empty")
        if self._is_conversation_stop_command(transcript):
            return VoiceTurnIntentDecision(
                intent="stop_conversation",
                reason="conversation_stop_command",
                close_continuous_dialog=True,
            )
        if self._is_likely_assistant_echo(controller, transcript):
            return VoiceTurnIntentDecision(
                intent="ignore",
                reason="assistant_echo",
                close_continuous_dialog=True,
            )
        return VoiceTurnIntentDecision(intent="voice_query", reason="sidecar_asr_ready_default")

    def _should_drop_invalid_raw_audio_segment(
        self,
        *,
        device_id: str,
        session_id: str,
        segment: SegmentBuffer,
    ) -> bool:
        """在进入 Omni 前丢弃明显异常的本地音频段。

        主要逻辑：
        1. 只检查本地确定事实，例如没有音频帧、没有 PCM 字节或时长极短。
        2. 不等待 ASR，也不依据 ASR 文本判断背景音，避免影响正常首响。
        3. 命中后关闭连续窗口，防止端侧继续等待本轮回复。

        参数：
            device_id/session_id: 当前设备和会话编号。
            segment: 当前语音段。

        返回值：
            `True` 表示已丢弃本轮，不应继续进入 Omni。
        """

        reason = ""
        if segment.frame_count <= 0 or not segment.payload:
            reason = "empty_audio_segment"
        elif segment.duration_ms() < 250:
            reason = "too_short_audio_segment"
        if not reason:
            return False
        self._close_segment_without_reply(
            device_id=device_id,
            session_id=session_id,
            segment=segment,
            reason=reason,
            close_continuous_dialog=True,
        )
        return True

    def _close_continuous_dialog_for_stop_command(
        self,
        *,
        device_id: str,
        session_id: str,
        segment: SegmentBuffer,
        transcript: str,
        source: str,
    ) -> None:
        """关闭端侧连续对话窗口并清理本轮预连接资源。

        主要逻辑：
        1. 向眼镜下发 `voice.dialog.close`，让端侧回到必须 WakeNet 唤醒的待命状态。
        2. 关闭本轮 Omni 预连接，避免停止指令继续生成模型回复。
        3. 如果已有播放流被模型提前创建，则通过用户打断路径中断播放。
        """

        if segment.omni_realtime_session is not None:
            try:
                try:
                    segment.omni_realtime_session.close(blocking=False)
                except TypeError:
                    segment.omni_realtime_session.close()
            except Exception as exc:  # noqa: BLE001 - 停止指令清理失败只写日志
                log_debug(
                    self._logger,
                    f"关闭停止指令 Omni 会话失败: segment_id={segment.segment_id} reason={exc!r}",
                    LogContext(device_id=device_id, session_id=session_id),
                )
            with self._lock:
                controller = self._controllers.get(device_id)
                if controller is not None and controller.persistent_omni_realtime_session is segment.omni_realtime_session:
                    controller.persistent_omni_realtime_session = None
        if segment.omni_realtime_context is not None:
            self.handle_user_interrupt(
                device_id=device_id,
                session_id=session_id,
                reason="conversation_stop_command",
                clear_queue=True,
            )
        self._discard_utterance_photo(device_id=device_id, session_id=session_id, segment=segment)
        self._send_control_message(
            device_id,
            "request",
            "voice.dialog.close",
            session_id,
            {
                "device_id": device_id,
                "reason": "conversation_stop_command",
                "transcript": transcript,
                "source": source,
            },
        )
        log_info(
            self._logger,
            (
                "已按用户指令关闭连续对话 "
                f"segment_id={segment.segment_id} input_stream_id={segment.stream_id} "
                f"source={source} transcript={_format_log_text(transcript)!r}"
            ),
            LogContext(device_id=device_id, session_id=session_id),
        )

    def _send_close_continuous_dialog_control(
        self,
        *,
        device_id: str,
        session_id: str,
        reason: str,
        source: str,
        stream_id: str | None = None,
    ) -> None:
        """向眼镜下发关闭连续对话窗口控制消息。

        主要逻辑：
        1. 统一封装 `voice.dialog.close` 的 payload，避免各路径字段不一致。
        2. 不修改播放队列，仅让端侧在当前窗口结束后回到 WakeNet 待命。
        3. 调用方负责决定关闭时机，例如模型工具请求可等当前播放结束后再调用。
        """

        payload: dict[str, Any] = {
            "device_id": device_id,
            "reason": reason,
            "source": source,
        }
        if stream_id:
            payload["stream_id"] = stream_id
        with self._lock:
            controller = self._controllers.get(device_id)
            persistent_session = controller.persistent_omni_realtime_session if controller else None
            if controller is not None:
                controller.persistent_omni_realtime_session = None
        if persistent_session is not None:
            persistent_session.close(blocking=False)
        self._send_control_message(
            device_id,
            "request",
            "voice.dialog.close",
            session_id,
            payload,
        )

    @staticmethod
    def _extract_close_continuous_dialog_request(meta: dict[str, Any]) -> dict[str, Any] | None:
        """从 Agent 结果中读取模型工具声明的连续对话关闭意图。"""

        turn_meta = meta.get("turn_meta")
        if not isinstance(turn_meta, dict):
            return None
        request = turn_meta.get("close_continuous_dialog")
        return request if isinstance(request, dict) and request.get("scheduled") else None

    def _schedule_close_continuous_dialog_after_reply(
        self,
        *,
        device_id: str,
        session_id: str,
        playback: PlaybackStreamContext,
        request: dict[str, Any],
    ) -> None:
        """根据模型工具请求安排当前回复播报结束后关闭连续对话。

        主要逻辑：
        1. 当前播放尚未结束时，只在控制器上记录待关闭信息。
        2. 如果播放已经结束，立即下发 `voice.dialog.close`。
        3. 关闭请求不打断当前回复，符合“当前响应播报完后退出”的交互预期。
        """

        reason = str(request.get("reason") or "model_requested").strip() or "model_requested"
        source = str(request.get("source") or "model_tool").strip() or "model_tool"
        if playback.finished_event.is_set() or playback.completed:
            self._send_close_continuous_dialog_control(
                device_id=device_id,
                session_id=session_id,
                reason=reason,
                source=source,
                stream_id=playback.stream_id,
            )
            return
        with self._lock:
            controller = self._controllers.get(device_id)
            if controller is None:
                return
            controller.close_continuous_dialog_after_stream_id = playback.stream_id
            controller.close_continuous_dialog_after_reason = reason
            controller.close_continuous_dialog_after_source = source
        log_info(
            self._logger,
            (
                "模型工具已请求回复后关闭连续对话 "
                f"stream_id={playback.stream_id} reason={reason} source={source}"
            ),
            LogContext(device_id=device_id, session_id=session_id),
        )

    def _close_continuous_dialog_after_playback_if_needed(
        self,
        *,
        device_id: str,
        session_id: str,
        stream_id: str,
    ) -> None:
        """在指定播放流结束后执行延迟关闭连续对话。"""

        reason: str | None = None
        source: str | None = None
        with self._lock:
            controller = self._controllers.get(device_id)
            if controller is None:
                return
            if controller.close_continuous_dialog_after_stream_id != stream_id:
                return
            reason = controller.close_continuous_dialog_after_reason or "model_requested"
            source = controller.close_continuous_dialog_after_source or "model_tool"
            controller.close_continuous_dialog_after_stream_id = None
            controller.close_continuous_dialog_after_reason = None
            controller.close_continuous_dialog_after_source = None
        self._send_close_continuous_dialog_control(
            device_id=device_id,
            session_id=session_id,
            reason=reason,
            source=source,
            stream_id=stream_id,
        )
        log_info(
            self._logger,
            (
                "当前回复播报完成后已关闭连续对话 "
                f"stream_id={stream_id} reason={reason} source={source}"
            ),
            LogContext(device_id=device_id, session_id=session_id),
        )

    def _should_stop_conversation_from_sidecar(
        self,
        *,
        device_id: str,
        session_id: str,
        segment: SegmentBuffer,
    ) -> bool:
        """在进入 Omni 回复前用旁路 ASR 拦截停止指令。

        主要逻辑：
        1. 只读取已经完成的旁路 ASR 最终文本，不等待 ASR。
        2. 如果文本是停止连续对话指令，则关闭端侧窗口并返回 True。
        3. 没有文本或不是停止指令时返回 False，让正常语音链路继续。
        """

        if not segment.sidecar_transcript_done.is_set():
            return False
        transcript = segment.sidecar_transcript_text.strip()
        if not self._is_conversation_stop_command(transcript):
            return False
        self._close_continuous_dialog_for_stop_command(
            device_id=device_id,
            session_id=session_id,
            segment=segment,
            transcript=transcript,
            source=segment.sidecar_transcript_source or "sidecar_asr",
        )
        return True

    def _schedule_sidecar_transcript_backfill(
        self,
        *,
        device_id: str,
        session_id: str,
        segment: SegmentBuffer,
        current_transcript: str,
        agent_result_meta: dict[str, Any],
        transcript_path: str,
    ) -> None:
        """在旁路 ASR 晚于 Omni 回复完成时回填用户文本。

        主要逻辑：
        1. 后台等待旁路 ASR 完成。
        2. 如果得到更可信的旁路文本，则更新 Agent 会话中的用户消息。
        3. 同步重写本轮 transcript artifact，便于离线排障看到最终文本来源。
        """

        if segment.sidecar_transcript_done.is_set():
            return
        user_message_id = str(agent_result_meta.get("user_message_id") or "")
        if not user_message_id:
            return

        def _worker() -> None:
            if not segment.sidecar_transcript_done.wait(max(5.0, self._settings.voice_model_timeout_ms / 1000)):
                return
            sidecar_text = segment.sidecar_transcript_text.strip()
            if not sidecar_text or sidecar_text == current_transcript.strip():
                return
            try:
                self._agent_facade.get_session_store().update_message_text(
                    session_id=session_id,
                    message_id=user_message_id,
                    text=sidecar_text,
                )
                with open(transcript_path, "w", encoding="utf-8") as file:
                    json.dump(
                        {
                            "segment_id": segment.segment_id,
                            "stream_id": segment.stream_id,
                            "transcript": sidecar_text,
                            "reply_mode": "omni_realtime",
                            "input_audio_streaming": True,
                            "transcript_source": segment.sidecar_transcript_source or "sidecar_asr",
                            "backfilled": True,
                        },
                        file,
                        ensure_ascii=False,
                        indent=2,
                    )
                log_info(
                    self._logger,
                    (
                        "旁路 ASR 转写已回填 Agent 会话 "
                        f"segment_id={segment.segment_id} source={segment.sidecar_transcript_source} "
                        f"text={_format_log_text(sidecar_text)!r}"
                    ),
                    LogContext(device_id=device_id, session_id=session_id),
                )
            except Exception as exc:  # noqa: BLE001 - 回填失败不能影响已完成回复
                log_debug(
                    self._logger,
                    f"旁路 ASR 转写回填失败: segment_id={segment.segment_id} reason={exc!r}",
                    LogContext(device_id=device_id, session_id=session_id),
                )

        threading.Thread(
            target=_worker,
            name=f"sidecar-asr-backfill-{segment.segment_id}",
            daemon=True,
        ).start()

    def _start_utterance_photo_capture(
        self,
        *,
        device_id: str,
        session_id: str,
        segment: SegmentBuffer,
        reason: str = "utterance_finished",
    ) -> None:
        """启动本轮语音关联的后台自动抓拍。

        主要逻辑：
        1. 从 AgentFacade 的 ToolRegistry 读取真实相机网关和语音照片缓存。
        2. 只启动后台任务，不等待端侧图片上传完成。
        3. 抓拍失败只写入缓存记录，不能阻塞 ASR 和大模型链路。

        参数：
        1. `device_id`：当前眼镜设备编号。
        2. `session_id`：当前控制会话编号。
        3. `segment`：当前语音段。
        4. `reason`：写入端侧抓拍请求的原因。

        返回值：
        1. 无返回值。

        异常情况：
        1. 未绑定相机网关或启动失败时只记录 DEBUG 日志，不影响语音主链路。
        """

        try:
            tool_registry = self._agent_facade.get_tool_registry()
            camera_gateway = tool_registry.get_camera_gateway()
            if camera_gateway is None:
                log_debug(
                    self._logger,
                    (
                        "跳过语音结束自动抓拍: CameraGateway 未绑定 "
                        f"segment_id={segment.segment_id} input_stream_id={segment.stream_id}"
                    ),
                    LogContext(device_id=device_id, session_id=session_id),
                )
                return
            tool_registry.get_utterance_photo_store().start_capture(
                camera_gateway=camera_gateway,
                session_id=session_id,
                device_id=device_id,
                segment_id=segment.segment_id,
                stream_id=segment.stream_id,
                timeout_ms=UTTERANCE_PHOTO_CAPTURE_TIMEOUT_MS,
                reason=reason,
            )
            segment.utterance_photo_capture_started = True
            log_debug(
                self._logger,
                (
                    "已启动语音自动抓拍 "
                    f"segment_id={segment.segment_id} input_stream_id={segment.stream_id} "
                    f"reason={reason} timeout_ms={UTTERANCE_PHOTO_CAPTURE_TIMEOUT_MS}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )
        except Exception as exc:  # noqa: BLE001 - 自动抓拍不能影响语音主链路
            log_debug(
                self._logger,
                (
                    "启动语音结束自动抓拍失败，继续语音主链路 "
                    f"segment_id={segment.segment_id} input_stream_id={segment.stream_id} error={exc}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )

    def _start_agent_core_omni_realtime_segment_session(
        self,
        *,
        device_id: str,
        session_id: str,
        segment: SegmentBuffer,
    ) -> None:
        """在语音段开始时启动 Agent-Core 原生音频 Realtime 会话。

        主要逻辑：
        1. 提前构造 Agent-Core instructions、工具 schema 和工具处理器。
        2. 建立 Omni Realtime WebSocket，让后续 `/ws_audio` PCM 分片直接转发给 Omni。
        3. 建连期间已经进入本地缓存的音频，会按原始顺序补推给 Omni。

        异常情况：
        1. 预连接失败只写日志并保留本地音频缓存，语音段结束后会回退到整段提交路径。
        """

        if segment.sample_rate != SERVER_SAMPLE_RATE_HZ or segment.channels != SERVER_CHANNELS:
            log_debug(
                self._logger,
                (
                    "跳过 Omni Realtime 输入预连接: 当前只支持 16k 单声道 PCM "
                    f"segment_id={segment.segment_id} sample_rate={segment.sample_rate} channels={segment.channels}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )
            return

        turn = AgentTurn(
            turn_id=generate_id("turn"),
            session_id=session_id,
            device_id=device_id,
            source="voice_raw_audio",
            input_text="用户发送了一段语音，请直接理解音频内容并执行用户意图。",
            meta={
                "segment_id": segment.segment_id,
                "stream_id": segment.stream_id,
                "reply_mode": "omni_realtime",
                "audio_streaming_mode": "input_audio_buffer.append",
            },
        )

        def _handle_progress_text(text: str) -> None:
            progress_text = text.strip()
            if progress_text:
                self._start_intermediate_reply(device_id=device_id, session_id=session_id, text=progress_text)

        try:
            prepared = self._agent_facade.prepare_native_audio_turn(
                turn,
                progress_callback=_handle_progress_text,
            )
            context: ReplySynthesisContext | None = None
            context_lock = threading.Lock()

            def _ensure_omni_reply_context() -> ReplySynthesisContext:
                """在 Omni 返回首段音频时再注册最终回复播放流。

                主要逻辑：
                1. 上行 Realtime 会话可以在语音段开始时建立。
                2. 下行最终回复播放流不能提前占用播放仲裁器，否则工具前置播报会被排队。
                3. 首个 `response.audio.delta` 到达时才创建最终播放流。
                """

                nonlocal context
                if context is not None:
                    return context
                with context_lock:
                    if context is None:
                        context = self._open_reply_synthesis_context(
                            device_id=device_id,
                            session_id=session_id,
                            audio_source="omni_realtime",
                        )
                        segment.omni_realtime_context = context
                    return context

            def _handle_omni_audio_chunk(chunk: ModelChunk) -> None:
                self._emit_synthesis_chunk(
                    device_id=device_id,
                    session_id=session_id,
                    context=_ensure_omni_reply_context(),
                    chunk=chunk,
                )

            def _handle_omni_audio_done() -> None:
                current_context = context
                if current_context is None:
                    return
                self._finalize_synthesis_context(
                    device_id=device_id,
                    session_id=session_id,
                    context=current_context,
                )

            use_persistent_omni = self._settings.voice_omni_session_lifecycle == "persistent"
            session: OmniRealtimeStreamingSession | None = None
            if use_persistent_omni:
                with self._lock:
                    controller = self._controllers.get(device_id)
                    session = controller.persistent_omni_realtime_session if controller else None
                if session is not None:
                    try:
                        session.begin_turn(
                            segment_id=segment.segment_id,
                            stream_id=segment.stream_id,
                            instructions=prepared.instructions,
                            tools=prepared.tools,
                            tool_handler=prepared.tool_handler,
                            on_chunk=_handle_omni_audio_chunk,
                            on_audio_done=_handle_omni_audio_done,
                            on_model_first_output=prepared.runtime.tool_context.note_model_output,
                        )
                    except Exception as exc:  # noqa: BLE001 - 长连接刷新失败时重建
                        log_debug(
                            self._logger,
                            (
                                "Omni Realtime 长连接刷新失败，准备重建 "
                                f"segment_id={segment.segment_id} reason={exc!r}"
                            ),
                            LogContext(device_id=device_id, session_id=session_id, message_id="omni_realtime"),
                        )
                        try:
                            session.close(blocking=False)
                        except Exception:
                            pass
                        with self._lock:
                            controller = self._controllers.get(device_id)
                            if controller is not None and controller.persistent_omni_realtime_session is session:
                                controller.persistent_omni_realtime_session = None
                        session = None
            if session is None:
                session = self._omni_realtime_client.start_streaming_reply(
                    settings=self._settings,
                    instructions=prepared.instructions,
                    on_chunk=_handle_omni_audio_chunk,
                    on_audio_done=_handle_omni_audio_done,
                    tools=prepared.tools,
                    tool_handler=prepared.tool_handler,
                    on_model_first_output=prepared.runtime.tool_context.note_model_output,
                    session_id=session_id,
                    device_id=device_id,
                    segment_id=segment.segment_id,
                    stream_id=segment.stream_id,
                )
                if use_persistent_omni:
                    with self._lock:
                        controller = self._controllers.get(device_id)
                        if controller is not None:
                            controller.persistent_omni_realtime_session = session
                    log_info(
                        self._logger,
                        (
                            "Omni Realtime 长连接已建立 "
                            f"segment_id={segment.segment_id} stream_id={segment.stream_id}"
                        ),
                        LogContext(device_id=device_id, session_id=session_id, message_id="omni_realtime"),
                    )
        except Exception as exc:  # noqa: BLE001 - 预连接失败时回退整段提交路径
            log_debug(
                self._logger,
                (
                    "Omni Realtime 输入预连接失败，将在语音结束后回退整段提交 "
                    f"segment_id={segment.segment_id} reason={exc!r}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )
            return

        sent_bytes = 0
        try:
            while True:
                with self._lock:
                    controller = self._controllers.get(device_id)
                    if controller is None or controller.current_segment is not segment:
                        session.close(blocking=False)
                        if self._settings.voice_omni_session_lifecycle == "persistent" and controller is not None:
                            controller.persistent_omni_realtime_session = None
                        return
                    pending = bytes(segment.payload[sent_bytes:])
                    if not pending:
                        segment.omni_realtime_session = session
                        segment.omni_realtime_prepared = prepared
                        segment.agent_turn = turn
                        break
                session.append_audio(pending)
                sent_bytes += len(pending)
            log_info(
                self._logger,
                (
                    "Omni Realtime 端到端输入流已启动 "
                    f"segment_id={segment.segment_id} stream_id={segment.stream_id} "
                    f"buffered_audio_bytes={sent_bytes} tool_count={len(prepared.tools)}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )
        except Exception as exc:  # noqa: BLE001 - 已建连但补推缓存失败时关闭会话并回退
            session.close()
            with self._lock:
                if segment.omni_realtime_session is session:
                    segment.omni_realtime_session = None
                    segment.omni_realtime_context = None
                    segment.omni_realtime_prepared = None
                    segment.agent_turn = None
            log_debug(
                self._logger,
                (
                    "Omni Realtime 输入预推缓存失败，将在语音结束后回退整段提交 "
                    f"segment_id={segment.segment_id} reason={exc!r}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )


    def _run_model_pipeline(self, device_id: str, session_id: str, segment: SegmentBuffer) -> None:
        controller = self._get_controller(device_id)
        input_wav = segment.to_wav_bytes()
        input_path = self._store_asset(session_id, "input", f"{segment.segment_id}.wav", input_wav)
        transcript_path = ""
        output_pcm = bytearray()
        playback_stream_id = f"reply_{uuid.uuid4().hex[:12]}"

        try:
            voice_input_mode = self._settings.effective_voice_input_mode()
            input_model_label = (
                self._settings.voice_asr_model_name
                if voice_input_mode == "asr_text"
                else f"{self._settings.voice_model_name}<native_audio_input>"
            )
            reply_model_label = (
                self._settings.voice_model_name
                if voice_input_mode == "raw_audio"
                else self._settings.agent_model_name
            )
            log_info(
                self._logger,
                (
                    "语音链路开始处理音频段 "
                    f"input_stream_id={segment.stream_id} segment_id={segment.segment_id} "
                    f"duration_ms={segment.duration_ms()} bytes={len(input_wav)} "
                    f"voice_input_mode={voice_input_mode} "
                    f"input_model={input_model_label} reply_model={reply_model_label}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )
            if self._settings.effective_voice_server_mode() == "omni_server":
                if voice_input_mode == "raw_audio":
                    if self._should_drop_invalid_raw_audio_segment(
                        device_id=device_id,
                        session_id=session_id,
                        segment=segment,
                    ):
                        with self._lock:
                            if controller.state == "model_running":
                                controller.state = "listening"
                        return
                    self._finish_sidecar_transcript_async(
                        device_id=device_id,
                        session_id=session_id,
                        segment=segment,
                        input_wav=input_wav,
                    )
                    if self._should_stop_conversation_from_sidecar(
                        device_id=device_id,
                        session_id=session_id,
                        segment=segment,
                    ):
                        with self._lock:
                            if controller.state == "model_running":
                                controller.state = "listening"
                        return
                    decision = self._decide_ready_sidecar_intent_before_omni(
                        controller=controller,
                        segment=segment,
                    )
                    if decision.intent == "ignore":
                        self._close_segment_without_reply(
                            device_id=device_id,
                            session_id=session_id,
                            segment=segment,
                            reason=decision.reason,
                            close_continuous_dialog=decision.close_continuous_dialog,
                        )
                        with self._lock:
                            if controller.state == "model_running":
                                controller.state = "listening"
                        return
                    self._prepare_utterance_photo_for_intent(
                        device_id=device_id,
                        session_id=session_id,
                        segment=segment,
                        decision=decision,
                    )
                if (
                    segment.omni_realtime_session is not None
                    and segment.omni_realtime_prepared is not None
                    and segment.agent_turn is not None
                ):
                    self._run_agent_core_omni_realtime_streaming_reply_pipeline(
                        controller=controller,
                        device_id=device_id,
                        session_id=session_id,
                        segment=segment,
                        input_path=input_path,
                        input_wav=input_wav,
                    )
                else:
                    self._run_agent_core_omni_realtime_reply_pipeline(
                        controller=controller,
                        device_id=device_id,
                        session_id=session_id,
                        segment=segment,
                        input_path=input_path,
                        input_wav=input_wav,
                    )
                return
            if voice_input_mode == "asr_text":
                user_text = self._transcribe_segment(
                    device_id=device_id,
                    session_id=session_id,
                    segment=segment,
                    input_wav=input_wav,
                ).strip()
            else:
                user_text = "用户发送了一段语音，请直接理解音频内容并执行用户意图。"
            if not user_text:
                raise build_error(
                    ErrorCode.INTERNAL_ERROR,
                    "当前轮用户语音输入为空，无法继续调用 agent-core",
                    details={"segment_id": segment.segment_id},
                )
            decision = self._decide_voice_turn_intent(controller=controller, segment=segment, transcript=user_text)
            if decision.intent == "stop_conversation":
                self._close_continuous_dialog_for_stop_command(
                    device_id=device_id,
                    session_id=session_id,
                    segment=segment,
                    transcript=user_text,
                    source="asr_text",
                )
                with self._lock:
                    if controller.state == "model_running":
                        controller.state = "listening"
                return
            if decision.intent == "ignore":
                self._close_segment_without_reply(
                    device_id=device_id,
                    session_id=session_id,
                    segment=segment,
                    reason=decision.reason,
                    close_continuous_dialog=decision.close_continuous_dialog,
                )
                with self._lock:
                    if controller.state == "model_running":
                        controller.state = "listening"
                return
            self._prepare_utterance_photo_for_intent(
                device_id=device_id,
                session_id=session_id,
                segment=segment,
                decision=decision,
            )
            log_debug(
                self._logger,
                f"语音输入文本: {user_text}",
                LogContext(device_id=device_id, session_id=session_id),
            )
            log_info(
                self._logger,
                (
                    "语音输入已准备进入 agent-core "
                    f"input_stream_id={segment.stream_id} segment_id={segment.segment_id} "
                    f"voice_input_mode={voice_input_mode} "
                    f"agent_model={reply_model_label} text_length={len(user_text)} "
                    f"audio_asset_bytes={len(input_wav)}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )
            transcript_path = self._store_artifact(
                session_id,
                "transcript",
                f"{segment.segment_id}.json",
                {
                    "segment_id": segment.segment_id,
                    "stream_id": segment.stream_id,
                    "transcript": user_text,
                    "voice_input_mode": voice_input_mode,
                },
            )
            turn = AgentTurn(
                turn_id=generate_id("turn"),
                session_id=session_id,
                device_id=device_id,
                source=f"voice_{voice_input_mode}",
                input_text=user_text,
                asset_refs=[
                    MediaAssetRef(
                        asset_id=generate_id("asset"),
                        session_id=session_id,
                        asset_type="audio",
                        storage_uri=input_path,
                        mime_type="audio/wav",
                        codec="pcm16le",
                        duration_ms=segment.duration_ms(),
                        bytes=len(input_wav),
                        source_stream_id=segment.stream_id,
                    )
                ],
                derived_artifacts=[
                    DerivedArtifact(
                        artifact_id=generate_id("artifact"),
                        session_id=session_id,
                        artifact_type="voice_transcript",
                        storage_uri=transcript_path,
                        text=user_text,
                        meta={
                            "segment_id": segment.segment_id,
                            "stream_id": segment.stream_id,
                            "voice_input_mode": voice_input_mode,
                        },
                    )
                ],
                meta={
                    "segment_id": segment.segment_id,
                    "stream_id": segment.stream_id,
                },
            )
            streamed_reply_parts: list[str] = []
            final_synthesis_context: ReplySynthesisContext | None = None
            final_tts_session: StreamingTtsSession | None = None
            final_synthesis_lock = threading.Lock()
            model_request_started_at_ms = self._now_ms()
            first_model_token_logged = False
            use_streaming_tts_output = True

            def _ensure_final_synthesis_context() -> ReplySynthesisContext:
                """在最终回复首段文本到达时再注册播放流。

                主要逻辑：
                1. TTS 会话可以在 Agent 请求前预热。
                2. 播放流不能提前注册到播放仲裁器，否则工具前置播报会被排队。
                3. 首个最终回复文本准备推给 TTS 时，再创建真正的最终回复播放流。

                返回值：
                1. 当前最终回复的播放流上下文。
                """

                nonlocal final_synthesis_context
                if final_synthesis_context is not None:
                    return final_synthesis_context
                with final_synthesis_lock:
                    if final_synthesis_context is None:
                        final_synthesis_context = self._open_reply_synthesis_context(
                            device_id=device_id,
                            session_id=session_id,
                            audio_source="tts",
                        )
                    return final_synthesis_context

            def _create_final_tts_session() -> StreamingTtsSession:
                """创建当前 Agent 最终回复使用的流式 TTS 会话。

                主要逻辑：
                1. 预热 TTS WebSocket，但不提前注册最终回复播放流。
                2. 真正收到最终回复文本后，再创建播放流并接收 TTS 音频。
                3. 该函数也用于预热 session 失效后的重建。

                返回值：
                1. 当前回复可用的 `StreamingTtsSession`。
                """

                return self._model_client.create_streaming_tts_session(
                    settings=self._settings,
                    on_chunk=lambda chunk: self._emit_synthesis_chunk(
                        device_id=device_id,
                        session_id=session_id,
                        context=_ensure_final_synthesis_context(),
                        chunk=chunk,
                    ),
                )

            def _push_text_to_final_tts(text_delta: str) -> None:
                """把文本推给当前预热 TTS，会话失效时重建一次。"""

                nonlocal final_tts_session
                if not use_streaming_tts_output:
                    return
                assert final_tts_session is not None
                context = _ensure_final_synthesis_context()
                self._mark_first_text_delta(context.playback)
                try:
                    final_tts_session.push_text(text_delta)
                except Exception as exc:
                    log_debug(
                        self._logger,
                        f"TTS 预热会话推送失败，重建后重试: reason={exc!r}",
                        LogContext(device_id=device_id, session_id=session_id, message_id=turn.turn_id),
                    )
                    final_tts_session = _create_final_tts_session()
                    final_tts_session.push_text(text_delta)

            def _handle_progress_text(text: str) -> None:
                progress_text = text.strip()
                if not progress_text:
                    return
                self._start_intermediate_reply(device_id=device_id, session_id=session_id, text=progress_text)

            def _handle_reply_text_delta(text_delta: str) -> None:
                nonlocal final_synthesis_context, final_tts_session, first_model_token_logged
                if not text_delta:
                    return
                if not first_model_token_logged:
                    first_model_token_logged = True
                    first_token_at_ms = self._now_ms()
                    log_info(
                        self._logger,
                        (
                            "大模型返回首个 token "
                            f"first_token_latency_ms={max(first_token_at_ms - model_request_started_at_ms, 0)} "
                            f"segment_id={segment.segment_id} input_stream_id={segment.stream_id} "
                            f"token_preview={text_delta[:24]!r}"
                        ),
                        LogContext(device_id=device_id, session_id=session_id),
                    )
                streamed_reply_parts.append(text_delta)
                _push_text_to_final_tts(text_delta)

            final_tts_session = _create_final_tts_session()
            log_info(
                self._logger,
                (
                    "TTS 预热已启动 "
                    "stream_id=pending_until_first_text "
                    f"before_agent_request_ms={max(self._now_ms() - model_request_started_at_ms, 0)}"
                ),
                LogContext(device_id=device_id, session_id=session_id, message_id=turn.turn_id),
            )

            agent_result = self._agent_facade.handle_turn(
                turn,
                progress_callback=_handle_progress_text,
                reply_text_delta_callback=_handle_reply_text_delta if use_streaming_tts_output else None,
            )
            capability_trace_ids = [trace.trace_id for trace in agent_result.capability_traces]
            log_debug(
                self._logger,
                f"Agent 输出: has_error={agent_result.error is not None} traces={capability_trace_ids}",
                LogContext(device_id=device_id, session_id=session_id, message_id=turn.turn_id),
            )
            if agent_result.error is not None:
                log_debug(
                    self._logger,
                    f"Agent 失败详情: error={agent_result.error} meta={agent_result.meta}",
                    LogContext(device_id=device_id, session_id=session_id, message_id=turn.turn_id),
                )
            assistant_text = "".join(streamed_reply_parts).strip() or agent_result.reply_text.strip() or "收到。"
            if final_synthesis_context is not None and final_tts_session is not None:
                if not streamed_reply_parts:
                    _push_text_to_final_tts(assistant_text)
                final_tts_session.finish()
                self._finalize_synthesis_context(
                    device_id=device_id,
                    session_id=session_id,
                    context=final_synthesis_context,
                )
                playback_stream_id = final_synthesis_context.stream_id
                output_pcm.extend(final_synthesis_context.output_pcm)
            else:
                final_synthesis_context = self._open_reply_synthesis_context(
                    device_id=device_id,
                    session_id=session_id,
                )
                playback_stream_id = final_synthesis_context.stream_id
                self._send_control_message(
                    device_id,
                    "notify",
                    "assistant.reply",
                    session_id,
                    {
                        "device_id": device_id,
                        "text": assistant_text,
                        "stream_id": playback_stream_id,
                    },
                )
                self._synthesize_text_into_context(
                    device_id=device_id,
                    session_id=session_id,
                    context=final_synthesis_context,
                    text=assistant_text,
                )
                playback_stream_id = final_synthesis_context.stream_id
                output_pcm.extend(final_synthesis_context.output_pcm)
                playback_stream_id = final_synthesis_context.stream_id

            if final_synthesis_context is not None:
                self._send_control_message(
                    device_id,
                    "notify",
                    "assistant.reply",
                    session_id,
                    {
                        "device_id": device_id,
                        "text": assistant_text,
                        "stream_id": playback_stream_id,
                    },
                )
            output_path = self._store_asset(
                session_id,
                "output",
                f"{playback_stream_id}.wav",
                build_wav_bytes(bytes(output_pcm), SERVER_SAMPLE_RATE_HZ, SERVER_CHANNELS),
            )
            log_info(
                self._logger,
                f"Agent 最终回复: {assistant_text}",
                LogContext(device_id=device_id, session_id=session_id),
            )
            if agent_result.assistant_message_id:
                self._agent_facade.attach_assistant_asset(
                    session_id=session_id,
                    assistant_message_id=agent_result.assistant_message_id,
                    asset=MediaAssetRef(
                        asset_id=generate_id("asset"),
                        session_id=session_id,
                        asset_type="audio",
                        storage_uri=output_path,
                        mime_type="audio/wav",
                        codec="pcm16le",
                        bytes=len(output_pcm),
                        source_stream_id=playback_stream_id,
                    ),
                )

            with self._lock:
                if (
                    final_synthesis_context is not None
                    and controller.current_playback is final_synthesis_context.playback
                ):
                    controller.state = "reply_streaming"

            log_debug(
                self._logger,
                (
                    f"语音回复已准备: device_id={device_id} transcript={user_text} "
                    f"input={input_path} transcript_artifact={transcript_path} output={output_path}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )
        except AppError as exc:
            self._fail_current_playback(device_id)
            with self._lock:
                controller.state = "failed"
            log_error(
                self._logger,
                f"语音链路失败: code={exc.code} message={exc.message} details={exc.details}",
                LogContext(device_id=device_id, session_id=session_id),
            )
        except Exception as exc:
            self._fail_current_playback(device_id)
            with self._lock:
                controller.state = "failed"
            log_error(
                self._logger,
                f"agent-core 或播放编排失败: {exc}",
                LogContext(device_id=device_id, session_id=session_id),
            )

    def _run_agent_core_omni_realtime_streaming_reply_pipeline(
        self,
        *,
        controller: VoiceSessionController,
        device_id: str,
        session_id: str,
        segment: SegmentBuffer,
        input_path: str,
        input_wav: bytes,
    ) -> None:
        """提交已预连接的 Omni Realtime 字节流并写回 Agent-Core。

        主要逻辑：
        1. 语音段开始后，音频分片已经通过 `append_audio` 持续发给 Omni。
        2. 语音段结束时只补充可用图片、commit 输入并等待 Omni 音频/文本输出。
        3. 将用户转写、助手文本、音频资产和工具轨迹写回 Agent-Core 会话。

        参数：
            controller: 当前设备语音控制器。
            device_id/session_id: 当前设备和会话编号。
            segment: 已结束的本地语音段。
            input_path/input_wav: 已落盘的输入音频路径与 WAV 字节。

        异常情况：
            Omni 提交、响应等待或 Agent-Core 落盘失败时抛出结构化异常，由上层统一播报错误。
        """

        omni_session = segment.omni_realtime_session
        prepared = segment.omni_realtime_prepared
        turn = segment.agent_turn
        if omni_session is None or prepared is None or turn is None:
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                "Omni Realtime 字节流会话未准备完成",
                details={"segment_id": segment.segment_id, "stream_id": segment.stream_id},
            )

        turn.asset_refs.append(
            MediaAssetRef(
                asset_id=generate_id("asset"),
                session_id=session_id,
                asset_type="audio",
                storage_uri=input_path,
                mime_type="audio/wav",
                codec="pcm16le",
                duration_ms=segment.duration_ms(),
                bytes=len(input_wav),
                source_stream_id=segment.stream_id,
            )
        )

        direct_image_asset_ids = set(turn.meta.get("direct_image_asset_ids") or [])
        image_frames: list[bytes] = []
        for asset in turn.asset_refs:
            if asset.asset_type != "image":
                continue
            if direct_image_asset_ids and asset.asset_id not in direct_image_asset_ids:
                continue
            try:
                with open(asset.storage_uri, "rb") as file_obj:
                    image_frames.append(file_obj.read())
            except OSError as exc:
                log_debug(
                    self._logger,
                    (
                        "Omni Realtime 读取直传图片失败，跳过该图片 "
                        f"asset_id={asset.asset_id} path={asset.storage_uri} reason={exc!r}"
                    ),
                    LogContext(device_id=device_id, session_id=session_id),
                )

        try:
            result = omni_session.finish(
                image_frames=image_frames,
                instructions=prepared.instructions,
                segment_finished_at_ms=self._now_ms(),
            )
        except AppError as exc:
            details = getattr(exc, "details", {}) or {}
            if details.get("reason") == OMNI_SEMANTIC_VAD_NO_AUTO_RESPONSE:
                self._close_segment_without_reply(
                    device_id=device_id,
                    session_id=session_id,
                    segment=segment,
                    reason=OMNI_SEMANTIC_VAD_NO_AUTO_RESPONSE,
                    close_continuous_dialog=True,
                )
                with self._lock:
                    if controller.state == "model_running":
                        controller.state = "listening"
                return
            raise
        finally:
            if self._settings.voice_omni_session_lifecycle != "persistent":
                omni_session.close(blocking=False)

        transcript, transcript_source = self._select_transcript_for_agent(
            segment=segment,
            omni_transcript=result.transcript,
        )
        if segment.turn_intent == "unknown":
            segment.turn_intent = "omni_semantic_vad"
            segment.turn_intent_reason = "model_turn_detection"
        context = segment.omni_realtime_context
        if context is None:
            context = self._open_reply_synthesis_context(
                device_id=device_id,
                session_id=session_id,
                audio_source="omni_realtime",
            )
            segment.omni_realtime_context = context
        native_result = NativeAudioReplyResult(
            assistant_text=result.assistant_text,
            transcript=transcript,
            response_id=result.response_id,
            meta={
                "reply_mode": "omni_realtime",
                "output_audio_source": "omni_realtime",
                "input_audio_streaming": True,
                "transcript_source": transcript_source,
                "omni_transcript": result.transcript,
                "sidecar_transcript_source": segment.sidecar_transcript_source,
                "sidecar_transcript_error": segment.sidecar_transcript_error,
                "voice_turn_intent": segment.turn_intent,
                "voice_turn_intent_reason": segment.turn_intent_reason,
            },
        )
        agent_result = self._agent_facade.complete_prepared_native_audio_turn(
            turn=turn,
            prepared=prepared,
            native_result=native_result,
        )
        assistant_text = agent_result.reply_text.strip() or "收到。"
        close_dialog_request = self._extract_close_continuous_dialog_request(agent_result.meta)
        if close_dialog_request is not None:
            self._schedule_close_continuous_dialog_after_reply(
                device_id=device_id,
                session_id=session_id,
                playback=context.playback,
                request=close_dialog_request,
            )
        transcript_path = self._store_artifact(
            session_id,
            "transcript",
            f"{segment.segment_id}.json",
            {
                "segment_id": segment.segment_id,
                "stream_id": segment.stream_id,
                "transcript": transcript,
                "transcript_source": transcript_source,
                "omni_transcript": result.transcript,
                "sidecar_transcript_source": segment.sidecar_transcript_source,
                "sidecar_transcript_error": segment.sidecar_transcript_error,
                "reply_mode": "omni_realtime",
                "input_audio_streaming": True,
                "response_id": result.response_id,
            },
        )
        if transcript_source != "sidecar_realtime_asr" and transcript_source != "sidecar_batch_asr":
            self._schedule_sidecar_transcript_backfill(
                device_id=device_id,
                session_id=session_id,
                segment=segment,
                current_transcript=transcript,
                agent_result_meta=agent_result.meta,
                transcript_path=transcript_path,
            )

        self._finalize_synthesis_context(device_id=device_id, session_id=session_id, context=context)
        output_pcm = bytes(context.output_pcm)
        output_path = self._store_asset(
            session_id,
            "output",
            f"{context.stream_id}.wav",
            build_wav_bytes(output_pcm, SERVER_SAMPLE_RATE_HZ, SERVER_CHANNELS),
        )
        self._send_control_message(
            device_id,
            "notify",
            "assistant.reply",
            session_id,
            {
                "device_id": device_id,
                "text": assistant_text,
                "stream_id": context.stream_id,
            },
        )
        if agent_result.assistant_message_id:
            self._agent_facade.attach_assistant_asset(
                session_id=session_id,
                assistant_message_id=agent_result.assistant_message_id,
                asset=MediaAssetRef(
                    asset_id=generate_id("asset"),
                    session_id=session_id,
                    asset_type="audio",
                    storage_uri=output_path,
                    mime_type="audio/wav",
                    codec="pcm16le",
                    bytes=len(output_pcm),
                    source_stream_id=context.stream_id,
                ),
            )
        with self._lock:
            if controller.current_playback is context.playback:
                controller.state = "reply_streaming"
        log_info(
            self._logger,
            f"Agent 最终回复: {assistant_text}",
            LogContext(device_id=device_id, session_id=session_id),
        )
        log_info(
            self._logger,
            (
                "Omni Realtime 文字交互 "
                f"user={_format_log_text(transcript)!r} assistant={_format_log_text(assistant_text)!r} "
                f"transcript_source={transcript_source} response_id={result.response_id}"
            ),
            LogContext(device_id=device_id, session_id=session_id),
        )
        log_debug(
            self._logger,
            (
                f"语音回复已准备: device_id={device_id} transcript={transcript} "
                f"input={input_path} transcript_artifact={transcript_path} output={output_path}"
            ),
            LogContext(device_id=device_id, session_id=session_id),
        )

    def _run_agent_core_omni_realtime_reply_pipeline(
        self,
        *,
        controller: VoiceSessionController,
        device_id: str,
        session_id: str,
        segment: SegmentBuffer,
        input_path: str,
        input_wav: bytes,
    ) -> None:
        """通过 Agent-Core 执行 Omni Realtime 音频直出。

        主要逻辑：
        1. `VoiceRuntime` 只负责把当前音频段包装成 `AgentTurn` 和播放上下文。
        2. Agent-Core 负责 instructions、memory、Skill、tools 和工具调用循环。
        3. Omni 返回的音频分片通过回调写入现有播放流，文字结果用于日志和上下文。
        """

        if segment.sample_rate != SERVER_SAMPLE_RATE_HZ or segment.channels != SERVER_CHANNELS:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "Omni Realtime 直出当前只支持 16k 单声道 PCM 输入",
                details={
                    "segment_id": segment.segment_id,
                    "sample_rate": segment.sample_rate,
                    "channels": segment.channels,
                },
            )

        output_pcm = bytearray()
        context = self._open_reply_synthesis_context(
            device_id=device_id,
            session_id=session_id,
            audio_source="omni_realtime",
        )

        def _handle_omni_audio_chunk(chunk: ModelChunk) -> None:
            self._emit_synthesis_chunk(
                device_id=device_id,
                session_id=session_id,
                context=context,
                chunk=chunk,
            )

        def _run_native_audio_reply(
            *,
            input_pcm: bytes,
            sample_rate_hz: int,
            image_frames: list[bytes],
            instructions: str,
            tools: list[dict[str, Any]],
            tool_handler: Callable[[dict[str, Any]], dict[str, Any]],
            on_audio_chunk: Callable[[ModelChunk], None] | None,
            session_id: str,
            device_id: str,
            turn_id: str,
            stream_id: str,
            segment_id: str,
            on_model_first_output: Callable[[str], None] | None = None,
        ) -> NativeAudioReplyResult:
            del turn_id, sample_rate_hz
            effective_settings = replace(self._settings, voice_conversation_mode="segment_turn")
            result = self._omni_realtime_client.run_reply(
                settings=effective_settings,
                input_pcm=input_pcm,
                image_frames=image_frames,
                instructions=instructions,
                on_chunk=on_audio_chunk or (lambda _chunk: None),
                tools=tools,
                tool_handler=tool_handler,
                on_model_first_output=on_model_first_output,
                session_id=session_id,
                device_id=device_id,
                segment_id=segment_id or segment.segment_id,
                stream_id=stream_id or segment.stream_id,
            )
            return NativeAudioReplyResult(
                assistant_text=result.assistant_text,
                transcript=result.transcript,
                response_id=result.response_id,
                meta={"reply_mode": "omni_realtime", "output_audio_source": "omni_realtime"},
            )

        turn = AgentTurn(
            turn_id=generate_id("turn"),
            session_id=session_id,
            device_id=device_id,
            source="voice_raw_audio",
            input_text="用户发送了一段语音，请直接理解音频内容并执行用户意图。",
            asset_refs=[
                MediaAssetRef(
                    asset_id=generate_id("asset"),
                    session_id=session_id,
                    asset_type="audio",
                    storage_uri=input_path,
                    mime_type="audio/wav",
                    codec="pcm16le",
                    duration_ms=segment.duration_ms(),
                    bytes=len(input_wav),
                    source_stream_id=segment.stream_id,
                )
            ],
            meta={
                "segment_id": segment.segment_id,
                "stream_id": segment.stream_id,
                "reply_mode": "omni_realtime",
            },
        )
        def _handle_progress_text(text: str) -> None:
            progress_text = text.strip()
            if not progress_text:
                return
            self._start_intermediate_reply(device_id=device_id, session_id=session_id, text=progress_text)

        agent_result = self._agent_facade.handle_turn(
            turn,
            progress_callback=_handle_progress_text,
            native_audio_reply_runner=_run_native_audio_reply,
            reply_audio_chunk_callback=_handle_omni_audio_chunk,
        )
        assistant_text = agent_result.reply_text.strip() or "收到。"
        close_dialog_request = self._extract_close_continuous_dialog_request(agent_result.meta)
        if close_dialog_request is not None:
            self._schedule_close_continuous_dialog_after_reply(
                device_id=device_id,
                session_id=session_id,
                playback=context.playback,
                request=close_dialog_request,
            )
        transcript = str(agent_result.meta.get("user_text_override") or "").strip()
        transcript_path = self._store_artifact(
            session_id,
            "transcript",
            f"{segment.segment_id}.json",
            {
                "segment_id": segment.segment_id,
                "stream_id": segment.stream_id,
                "transcript": transcript,
                "reply_mode": "omni_realtime",
                "response_id": agent_result.meta.get("native_audio_response_id"),
            },
        )
        self._finalize_synthesis_context(
            device_id=device_id,
            session_id=session_id,
            context=context,
        )
        output_pcm.extend(context.output_pcm)
        output_path = self._store_asset(
            session_id,
            "output",
            f"{context.stream_id}.wav",
            build_wav_bytes(bytes(output_pcm), SERVER_SAMPLE_RATE_HZ, SERVER_CHANNELS),
        )
        self._send_control_message(
            device_id,
            "notify",
            "assistant.reply",
            session_id,
            {
                "device_id": device_id,
                "text": assistant_text,
                "stream_id": context.stream_id,
            },
        )
        if agent_result.assistant_message_id:
            self._agent_facade.attach_assistant_asset(
                session_id=session_id,
                assistant_message_id=agent_result.assistant_message_id,
                asset=MediaAssetRef(
                    asset_id=generate_id("asset"),
                    session_id=session_id,
                    asset_type="audio",
                    storage_uri=output_path,
                    mime_type="audio/wav",
                    codec="pcm16le",
                    bytes=len(output_pcm),
                    source_stream_id=context.stream_id,
                ),
            )
        with self._lock:
            if controller.current_playback is context.playback:
                controller.state = "reply_streaming"
        log_info(
            self._logger,
            f"Agent 最终回复: {assistant_text}",
            LogContext(device_id=device_id, session_id=session_id),
        )
        log_info(
            self._logger,
            (
                "Omni Realtime 文字交互 "
                f"user={_format_log_text(transcript)!r} assistant={_format_log_text(assistant_text)!r} "
                f"response_id={agent_result.meta.get('native_audio_response_id')}"
            ),
            LogContext(device_id=device_id, session_id=session_id),
        )
        log_debug(
            self._logger,
            (
                f"语音回复已准备: device_id={device_id} transcript={transcript} "
                f"input={input_path} transcript_artifact={transcript_path} output={output_path}"
            ),
            LogContext(device_id=device_id, session_id=session_id),
        )

    def _build_model_messages(self, controller: VoiceSessionController, user_text: str) -> list[dict[str, Any]]:
        """组装模型消息列表。

        主要逻辑：
        1. 固定注入系统提示词。
        2. 回放最近若干轮短期上下文，把历史用户音频从 `asset_refs` 解析成模型可读的 `input_audio`。
        3. 把当前轮用户输入追加到末尾。

        参数：
        1. `controller`：当前设备语音会话控制器。
        2. `user_text`：当前轮用户语音经 ASR 转写后的文本。

        返回值：
        1. 可直接提交给多模态模型的 `messages`。
        """

        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": self._settings.voice_system_prompt,
            }
        ]
        history = controller.message_context[-6:]
        for entry in history:
            built_message = self._build_history_message(entry)
            if built_message is not None:
                messages.append(built_message)
        messages.append(
            {
                "role": "user",
                "content": user_text,
            }
        )
        return messages

    def _build_history_message(self, entry: MessageEntry) -> dict[str, Any] | None:
        """把单条历史消息转换为模型可读格式。

        主要逻辑：
        1. 对历史用户语音消息，回读最近一份输入 WAV 并组装为 `input_audio`。
        2. 对历史助手回复，直接传递文本。
        3. 对无有效内容的消息返回 `None`。

        参数：
        1. `entry`：消息上下文条目。

        返回值：
        1. 可直接放进 `messages` 的字典；若无有效内容则返回 `None`。
        """

        if entry.text:
            return {"role": entry.role, "content": entry.text}
        return None

    def _play_intermediate_reply(self, device_id: str, session_id: str, text: str) -> None:
        """异步播报一段中间提示语。"""

        try:
            context = self._create_intermediate_reply_context(device_id=device_id, session_id=session_id, text=text)
            self._synthesize_text_into_context(
                device_id=device_id,
                session_id=session_id,
                context=context,
                text=text,
            )
        except Exception as exc:  # pragma: no cover - 真实联调路径
            log_debug(
                self._logger,
                f"中间播报失败，已忽略: reason={exc!r}",
                LogContext(device_id=device_id, session_id=session_id),
            )

    def _start_intermediate_reply(self, *, device_id: str, session_id: str, text: str) -> None:
        """同步注册中间播报播放流，并异步执行 TTS 合成。

        主要逻辑：
        1. 先同步创建播放流，让播放仲裁器立刻知道有一条前置播报。
        2. 再把 TTS 合成放到后台线程，避免阻塞工具执行。
        3. 这样最终回复首 token 到达时，会排在前置播报之后，而不是抢先占用播放通道。

        参数：
        1. `device_id`：目标眼镜设备编号。
        2. `session_id`：当前语音会话编号。
        3. `text`：要播报的短提示。

        异常情况：
        1. 创建播放流失败时只记录 DEBUG 日志，不打断工具调用。
        """

        try:
            context = self._create_intermediate_reply_context(device_id=device_id, session_id=session_id, text=text)
        except Exception as exc:  # pragma: no cover - 真实联调路径
            log_debug(
                self._logger,
                f"中间播报启动失败，已忽略: reason={exc!r}",
                LogContext(device_id=device_id, session_id=session_id),
            )
            return
        threading.Thread(
            target=self._play_intermediate_reply_context,
            kwargs={
                "device_id": device_id,
                "session_id": session_id,
                "context": context,
                "text": text,
            },
            daemon=True,
        ).start()

    def _play_intermediate_reply_context(
        self,
        *,
        device_id: str,
        session_id: str,
        context: ReplySynthesisContext,
        text: str,
        ) -> None:
        """按配置播放中间提示，保持统一的下行播放流框架。

        主要逻辑：
        1. 主回复是 Omni Realtime 音频直出时，前置播报也调用同一个 Omni Realtime 模型和音色。
        2. 主回复是独立 TTS 时，`cached` 模式优先读取启动阶段预生成音频。
        3. TTS `realtime` 模式跳过缓存，每次工具调用时创建流式 TTS 会话。
        4. 所有模式最终都写入同一个 `ReplySynthesisContext` 和播放队列。
        """

        progress_provider = self._progress_audio_provider()
        if progress_provider == "omni_realtime":
            log_info(
                self._logger,
                (
                    "工具前置播报使用 Omni Realtime "
                    f"stream_id={context.stream_id} mode={self._settings.tool_progress_audio_mode} "
                    f"model={self._settings.voice_omni_realtime_model_name} voice={self._settings.voice_model_voice}"
                ),
                LogContext(device_id=device_id, session_id=session_id, message_id=context.stream_id),
            )
            self._synthesize_omni_text_into_context(
                device_id=device_id,
                session_id=session_id,
                context=context,
                text=text,
            )
            return

        if self._settings.tool_progress_audio_mode == "cached":
            cached_pcm = self._get_cached_progress_pcm(text)
            if cached_pcm:
                self._emit_cached_progress_pcm(
                    device_id=device_id,
                    session_id=session_id,
                    context=context,
                    pcm_bytes=cached_pcm,
                )
                return
        else:
            log_info(
                self._logger,
                (
                    "工具前置播报使用实时流式 TTS "
                    f"stream_id={context.stream_id} mode={self._settings.tool_progress_audio_mode} "
                    f"model={self._settings.tts_model_name} voice={self._settings.tts_voice}"
                ),
                LogContext(device_id=device_id, session_id=session_id, message_id=context.stream_id),
            )
        self._synthesize_text_into_context(
            device_id=device_id,
            session_id=session_id,
            context=context,
            text=text,
        )

    def _get_cached_progress_pcm(self, text: str) -> bytes | None:
        """读取已预生成的前置播报 PCM。"""

        normalized = text.strip()
        if not normalized:
            return None
        if not self._progress_audio_cache_ready.is_set():
            self._progress_audio_cache_ready.wait(timeout=0.05)
        with self._progress_audio_cache_lock:
            entry = self._progress_audio_cache.get(normalized)
            return entry.pcm_bytes if entry is not None else None

    def _emit_cached_progress_pcm(
        self,
        *,
        device_id: str,
        session_id: str,
        context: ReplySynthesisContext,
        pcm_bytes: bytes,
    ) -> None:
        """把缓存 PCM 通过统一音频分片入口写入播放流。"""

        if context.playback.first_audio_chunk_at_ms is None:
            log_info(
                self._logger,
                (
                    "工具前置播报命中静态音频缓存 "
                    f"stream_id={context.stream_id} pcm_bytes={len(pcm_bytes)}"
                ),
                LogContext(device_id=device_id, session_id=session_id, message_id=context.stream_id),
            )
        self._emit_synthesis_chunk(
            device_id=device_id,
            session_id=session_id,
            context=context,
            chunk=ModelChunk(audio_pcm_bytes=pcm_bytes, sample_rate_hz=SERVER_SAMPLE_RATE_HZ),
        )
        self._finalize_synthesis_context(device_id=device_id, session_id=session_id, context=context)

    def _create_intermediate_reply_context(
        self,
        *,
        device_id: str,
        session_id: str,
        text: str,
    ) -> ReplySynthesisContext:
        """创建中间播报播放流并发送文本控制消息。"""

        context = self._open_reply_synthesis_context(
            device_id=device_id,
            session_id=session_id,
            source="agent_progress",
            audio_source=self._progress_audio_provider(),
        )
        self._send_control_message(
            device_id,
            "notify",
            "assistant.reply",
            session_id,
            {
                "device_id": device_id,
                "text": text,
                "stream_id": context.stream_id,
            },
        )
        return context

    def _dispatch_notification_request(self, request: NotificationRequest) -> None:
        """把通过裁决的通知申请转成实际播报。"""

        threading.Thread(
            target=self._play_notification_request,
            args=(request,),
            daemon=True,
        ).start()

    def _play_notification_request(self, request: NotificationRequest) -> None:
        """播报通知协调器批准的通知。"""

        text = str(request.payload.get("text", "")).strip()
        if not text:
            return
        try:
            context = self._open_reply_synthesis_context(
                device_id=request.device_id,
                session_id=request.session_id,
                source="vision_alert" if request.notification_type.startswith("vision.") else "task_notification",
                priority=request.priority,
                interrupt_policy=request.interrupt_policy,
                resume_policy=request.resume_policy,
                task_id=request.task_id,
            )
            with self._lock:
                self._notification_stream_requests[(request.device_id, context.stream_id)] = request.request_id
                self._notification_request_streams[request.request_id] = (request.device_id, context.stream_id)
            self._send_control_message(
                request.device_id,
                "notify",
                "assistant.reply",
                request.session_id,
                {
                    "device_id": request.device_id,
                    "text": text,
                    "stream_id": context.stream_id,
                    "task_id": request.task_id,
                    "task_type": request.payload.get("task_type"),
                    "task_state": request.payload.get("task_state"),
                    "priority": request.priority,
                    "interrupt_policy": request.interrupt_policy,
                    "resume_policy": request.resume_policy,
                },
            )
            self._synthesize_text_into_context(
                device_id=request.device_id,
                session_id=request.session_id,
                context=context,
                text=text,
            )
        except Exception as exc:  # pragma: no cover - 真机联调路径
            log_debug(
                self._logger,
                f"通知播报失败，已忽略: reason={exc!r}",
                LogContext(device_id=request.device_id, session_id=request.session_id, message_id=request.request_id),
            )

    def _run_task_event_agent_turn(self, event: TaskEvent, dispatched_direct_notify: bool) -> None:
        """执行后台任务事件的 Agent 回流主路径。"""

        try:
            turn = self._task_event_bridge.convert_event_to_agent_turn(event)
            agent_result = self._agent_facade.handle_turn(turn)
            reply_text = agent_result.reply_text.strip()
            if not reply_text or dispatched_direct_notify:
                return
            self._notification_coordinator.submit(
                NotificationRequest(
                    request_id=generate_id("notify_req"),
                    source_module="agent-core",
                    session_id=event.session_id,
                    device_id=event.device_id,
                    task_id=event.task_id,
                    priority=event.priority,
                    notification_type=f"{event.event_name}.agent_reply",
                    delivery_mode="audio",
                    allow_interrupt=event.priority in {"high", "critical"},
                    allow_merge=event.priority in {"low", "normal"},
                    requires_agent_context_sync=False,
                    dedupe_key=f"{event.event_name}:{event.task_id}:agent_reply",
                    payload={
                        "text": reply_text,
                        "task_type": event.task_type,
                        "task_state": event.state,
                    },
                )
            )
        except Exception as exc:  # pragma: no cover - 真机联调路径
            log_debug(
                self._logger,
                f"任务事件回流 agent-core 失败，已忽略: reason={exc!r}",
                LogContext(device_id=event.device_id, session_id=event.session_id, message_id=event.event_id),
            )

    def _interrupt_notification_request(self, request: NotificationRequest) -> None:
        """中断当前活动的通知播报流。

        主要逻辑：
        1. 根据通知编号找到对应播放流。
        2. 只摘除当前通知对应的播放流，不清空普通回复待播队列。
        3. 先向设备显式下发 `actuator.audio.interrupt`，再让新的高优先级通知接管活动位置。
        """

        playback: PlaybackStreamContext | None = None
        interrupt_device_id: str | None = None
        interrupt_session_id: str | None = None
        interrupt_stream_id: str | None = None
        with self._lock:
            stream_ref = self._notification_request_streams.pop(request.request_id, None)
            if stream_ref is None:
                return
            device_id, stream_id = stream_ref
            interrupt_device_id = device_id
            interrupt_stream_id = stream_id
            self._notification_stream_requests.pop((device_id, stream_id), None)
            playback = self._playback_streams.pop((device_id, stream_id), None)
            self._playback_arbiter.remove(device_id=device_id, stream_id=stream_id)
            controller = self._controllers.get(device_id)
            if playback is None or controller is None:
                return
            interrupt_session_id = playback.session_id
            self._mark_playback_interrupted_locked(
                controller=controller,
                playback=playback,
                reason="higher_priority_notification",
            )
        if (
            interrupt_device_id is not None
            and interrupt_session_id is not None
            and interrupt_stream_id is not None
        ):
            self._send_control_message(
                interrupt_device_id,
                "request",
                "actuator.audio.interrupt",
                interrupt_session_id,
                {
                    "device_id": interrupt_device_id,
                    "stream_id": interrupt_stream_id,
                    "reason": "higher_priority_notification",
                    "request_id": request.request_id,
                    "resume_policy": request.resume_policy,
                },
            )
        try:
            playback.queue.put_nowait(None)
        except queue.Full:
            pass

    def _synthesize_text_into_context(
        self,
        *,
        device_id: str,
        session_id: str,
        context: ReplySynthesisContext,
        text: str,
    ) -> None:
        """把完整文本合成到指定播放流上下文。

        主要逻辑：
        1. 优先走统一的流式 TTS 会话接口。
        2. 即使当前只有完整文本，也按“推送文本 -> 完成”的方式进入 TTS，
           这样中间播报与最终播报都能复用 CosyVoice 流式链路。
        """

        tts_session = self._model_client.create_streaming_tts_session(
            settings=self._settings,
            on_chunk=lambda chunk: self._emit_synthesis_chunk(
                device_id=device_id,
                session_id=session_id,
                context=context,
                chunk=chunk,
            ),
        )
        self._mark_first_text_delta(context.playback)
        tts_session.push_text(text)
        tts_session.finish()
        self._finalize_synthesis_context(device_id=device_id, session_id=session_id, context=context)

    def _synthesize_omni_text_into_context(
        self,
        *,
        device_id: str,
        session_id: str,
        context: ReplySynthesisContext,
        text: str,
    ) -> None:
        """用 Omni Realtime 生成工具前置播报并写入播放流。

        主要逻辑：
        1. 当前主回复链路如果是 Omni Realtime 音频直出，前置提示也必须由同一个 Omni 模型生成。
        2. 这里只把固定提示文本交给 Omni 朗读，不追加用户音频、不暴露工具。
        3. Omni 返回的音频分片继续走 `_emit_synthesis_chunk`，保持与最终回复相同的播放框架。

        异常情况：
            Omni 调用失败时向上抛出结构化错误，由调用线程记录失败；不会影响工具本身继续执行。
        """

        self._mark_first_text_delta(context.playback)
        self._omni_realtime_client.synthesize_text_audio(
            settings=replace(self._settings, voice_conversation_mode="segment_turn"),
            text=text,
            on_chunk=lambda chunk: self._emit_synthesis_chunk(
                device_id=device_id,
                session_id=session_id,
                context=context,
                chunk=chunk,
            ),
            session_id=session_id,
            device_id=device_id,
            stream_id=context.stream_id,
        )
        self._finalize_synthesis_context(device_id=device_id, session_id=session_id, context=context)

    @staticmethod
    def _now_ms() -> int:
        """返回当前毫秒时间戳。"""

        return int(time.time() * 1000)

    def _mark_first_text_delta(self, playback: PlaybackStreamContext) -> None:
        """记录当前播放流收到首个文本增量的时间。"""

        if playback.first_text_delta_at_ms is None:
            playback.first_text_delta_at_ms = self._now_ms()

    def _open_reply_synthesis_context(
        self,
        *,
        device_id: str,
        session_id: str,
        source: str = "agent_reply",
        priority: str = "normal",
        interrupt_policy: str = "never",
        resume_policy: str = "drop_interrupted",
        task_id: str | None = None,
        audio_source: str = "tts",
    ) -> ReplySynthesisContext:
        """创建一条新的回复播放流上下文。"""

        stream_id = f"reply_{uuid.uuid4().hex[:12]}"
        playback = self._create_playback_stream(
            device_id=device_id,
            session_id=session_id,
            stream_id=stream_id,
            source=source,
            priority=priority,
            interrupt_policy=interrupt_policy,
            resume_policy=resume_policy,
            task_id=task_id,
            audio_source=audio_source,
        )
        return ReplySynthesisContext(stream_id=stream_id, playback=playback, audio_source=audio_source)

    def _request_playback_start(
        self,
        *,
        device_id: str,
        session_id: str,
        playback: PlaybackStreamContext,
        force: bool,
    ) -> None:
        """按当前播放队列状态决定是否下发播放请求。

        主要逻辑：
        1. 只有当前激活播放流才能真正启动播放。
        2. 已经下发过 `actuator.audio.play` 的流不重复下发。
        3. 对排队中的后续流，仅缓存音频，待前序流结束后再启动。

        参数：
        1. `device_id`：设备编号。
        2. `session_id`：会话编号。
        3. `playback`：目标播放流。
        4. `force`：是否在已有音频时立即启动。
        """

        should_send = False
        with self._lock:
            controller = self._controllers.get(device_id)
            if controller is None:
                raise build_error(
                    ErrorCode.STREAM_NOT_FOUND,
                    "未找到对应设备的语音会话控制器",
                    details={"device_id": device_id},
                )
            if controller.current_playback is playback and not playback.play_requested and force:
                playback.play_requested = True
                if playback.first_play_request_at_ms is None:
                    playback.first_play_request_at_ms = self._now_ms()
                should_send = True

        if should_send:
            self._send_control_message(
                device_id,
                "request",
                "actuator.audio.play",
                session_id,
                {
                    "mode": "stream",
                    "stream_id": playback.stream_id,
                    "format": "pcm16",
                    "sample_rate": SERVER_SAMPLE_RATE_HZ,
                    "channels": SERVER_CHANNELS,
                    "interrupt_policy": "forbid",
                },
            )
            log_info(
                self._logger,
                (
                    "下行播放请求已发送 "
                    f"stream_id={playback.stream_id} audio_source={playback.audio_source} "
                    f"text_to_play_request_ms={self._latency_ms(start=playback.first_text_delta_at_ms, end=playback.first_play_request_at_ms)} "
                    f"source_audio_to_play_request_ms={self._latency_ms(start=playback.first_audio_chunk_at_ms, end=playback.first_play_request_at_ms)}"
                ),
                LogContext(device_id=device_id, session_id=session_id, message_id=playback.stream_id),
            )

    def _emit_synthesis_chunk(
        self,
        *,
        device_id: str,
        session_id: str,
        context: ReplySynthesisContext,
        chunk: ModelChunk,
    ) -> None:
        """把下行音频分片推入当前播放流。"""

        if not chunk.audio_pcm_bytes:
            return
        if context.resampler is None or chunk.sample_rate_hz != context.resampler._input_rate_hz:
            context.resampler = PCM16StreamResampler(chunk.sample_rate_hz, SERVER_SAMPLE_RATE_HZ)
        pcm_chunk = context.resampler.push(chunk.audio_pcm_bytes, final=False)
        if not pcm_chunk:
            return
        if context.playback.first_audio_chunk_at_ms is None:
            context.playback.first_audio_chunk_at_ms = self._now_ms()
            log_info(
                self._logger,
                (
                    "下行音频源返回首段音频 "
                    f"stream_id={context.stream_id} audio_source={context.audio_source} "
                    f"input_sample_rate_hz={chunk.sample_rate_hz} "
                    f"pcm_bytes={len(chunk.audio_pcm_bytes)} output_bytes={len(pcm_chunk)} "
                    f"text_to_first_audio_ms={self._latency_ms(start=context.playback.first_text_delta_at_ms, end=context.playback.first_audio_chunk_at_ms)}"
                ),
                LogContext(device_id=device_id, session_id=session_id, message_id=context.stream_id),
            )
        self._enqueue_playback_chunk(context.playback, pcm_chunk)
        context.output_pcm.extend(pcm_chunk)
        self._request_playback_start(
            device_id=device_id,
            session_id=session_id,
            playback=context.playback,
            force=True,
        )

    def _finalize_synthesis_context(
        self,
        *,
        device_id: str,
        session_id: str,
        context: ReplySynthesisContext,
    ) -> None:
        """结束回复播放流，并补齐重采样尾巴。"""

        if context.finalized:
            return
        context.finalized = True

        if context.resampler is not None:
            tail_chunk = context.resampler.push(b"", final=True)
            if tail_chunk:
                self._enqueue_playback_chunk(context.playback, tail_chunk)
                context.output_pcm.extend(tail_chunk)
                self._request_playback_start(
                    device_id=device_id,
                    session_id=session_id,
                    playback=context.playback,
                    force=True,
                )

        if not context.playback.play_requested:
            silent_chunk = b"\x00" * 640
            self._enqueue_playback_chunk(context.playback, silent_chunk)
            context.output_pcm.extend(silent_chunk)
            self._request_playback_start(
                device_id=device_id,
                session_id=session_id,
                playback=context.playback,
                force=True,
            )

        self._finish_playback_stream(context.playback)

    def _create_playback_stream(
        self,
        *,
        device_id: str,
        session_id: str,
        stream_id: str,
        source: str = "agent_reply",
        priority: str = "normal",
        interrupt_policy: str = "never",
        resume_policy: str = "drop_interrupted",
        task_id: str | None = None,
        audio_source: str = "tts",
    ) -> PlaybackStreamContext:
        intent_id = f"{source}:{stream_id}"
        playback = PlaybackStreamContext(
            device_id=device_id,
            session_id=session_id,
            stream_id=stream_id,
            sample_rate=SERVER_SAMPLE_RATE_HZ,
            channels=SERVER_CHANNELS,
            source=source,
            audio_source=audio_source,
            priority=priority,
            interrupt_policy=interrupt_policy,
            resume_policy=resume_policy,
            task_id=task_id,
            intent_id=intent_id,
        )
        interrupted_playback: PlaybackStreamContext | None = None
        with self._lock:
            controller = self._controllers.get(device_id)
            if controller is None:
                raise build_error(
                    ErrorCode.STREAM_NOT_FOUND,
                    "未找到对应设备的语音会话控制器",
                    details={"device_id": device_id},
                )
            intent = PlaybackIntent(
                intent_id=intent_id,
                source=source,
                device_id=device_id,
                session_id=session_id,
                stream_id=stream_id,
                priority=priority,
                interrupt_policy=interrupt_policy,
                resume_policy=resume_policy,
                task_id=task_id,
            )
            submit_result = self._playback_arbiter.submit(intent)
            if submit_result.interrupted_intent is not None:
                interrupted_stream_id = submit_result.interrupted_intent.stream_id
                interrupted_playback = self._playback_streams.pop((device_id, interrupted_stream_id), None)
                if interrupted_playback is not None:
                    self._mark_playback_interrupted_locked(
                        controller=controller,
                        playback=interrupted_playback,
                        reason="higher_priority_playback",
                    )
                    request_id = self._notification_stream_requests.pop((device_id, interrupted_stream_id), None)
                    if request_id is not None:
                        self._notification_request_streams.pop(request_id, None)
            if submit_result.decision.action in {"play_now", "interrupt"}:
                controller.current_playback = playback
            else:
                controller.pending_playbacks.append(playback)
                controller.pending_playbacks.sort(
                    key=lambda item: (-self._playback_priority_value(item.priority), item.created_at_ms)
                )
            self._playback_streams[(device_id, stream_id)] = playback
            self._playback_condition.notify_all()
        if interrupted_playback is not None:
            self._send_control_message(
                device_id,
                "request",
                "actuator.audio.interrupt",
                interrupted_playback.session_id,
                {
                    "device_id": device_id,
                    "stream_id": interrupted_playback.stream_id,
                    "reason": "higher_priority_playback",
                    "incoming_stream_id": stream_id,
                    "resume_policy": interrupted_playback.resume_policy,
                },
            )
            try:
                interrupted_playback.queue.put_nowait(None)
            except queue.Full:
                pass
        return playback

    def _enqueue_playback_chunk(self, playback: PlaybackStreamContext, chunk: bytes) -> None:
        while True:
            try:
                playback.queue.put(chunk, timeout=0.5)
                return
            except queue.Full:
                if playback.abort_event.is_set():
                    return

    def _finish_playback_stream(self, playback: PlaybackStreamContext) -> None:
        playback.completed = True
        playback.finished_event.set()
        try:
            playback.queue.put_nowait(None)
        except queue.Full:
            pass

    def _fail_current_playback(self, device_id: str) -> None:
        with self._lock:
            controller = self._controllers.get(device_id)
            if controller is None or controller.current_playback is None:
                return
            playback = controller.current_playback
            playback.failed = True
            playback.completed = True
            playback.abort_event.set()
            playback.finished_event.set()
            controller.current_playback = None
            for pending in controller.pending_playbacks:
                pending.failed = True
                pending.completed = True
                pending.abort_event.set()
                pending.finished_event.set()
                self._playback_streams.pop((device_id, pending.stream_id), None)
                self._playback_arbiter.remove(device_id=device_id, stream_id=pending.stream_id)
                request_id = self._notification_stream_requests.pop((device_id, pending.stream_id), None)
                if request_id is not None:
                    self._notification_request_streams.pop(request_id, None)
                try:
                    pending.queue.put_nowait(None)
                except queue.Full:
                    pass
            controller.pending_playbacks.clear()
            controller.state = "failed"
            self._playback_streams.pop((device_id, playback.stream_id), None)
            self._playback_arbiter.remove(device_id=device_id, stream_id=playback.stream_id)
            request_id = self._notification_stream_requests.pop((device_id, playback.stream_id), None)
            if request_id is not None:
                self._notification_request_streams.pop(request_id, None)
        try:
            playback.queue.put_nowait(None)
        except queue.Full:
            pass

    def _playback_priority_value(self, priority: str) -> int:
        """把播放优先级转换为本地队列排序值。"""

        return {
            "low": 0,
            "normal": 1,
            "high": 2,
            "critical": 3,
        }.get(priority, 1)

    def _pop_pending_playback_locked(
        self,
        controller: VoiceSessionController,
        stream_id: str,
    ) -> PlaybackStreamContext | None:
        """按播放流编号从待播队列取出下一条播放流。"""

        for index, pending in enumerate(controller.pending_playbacks):
            if pending.stream_id == stream_id:
                return controller.pending_playbacks.pop(index)
        return None

    def _mark_playback_interrupted_locked(
        self,
        *,
        controller: VoiceSessionController,
        playback: PlaybackStreamContext,
        reason: str,
    ) -> None:
        """在持锁状态下把播放流标记为已中断。"""

        playback.failed = True
        playback.completed = True
        playback.abort_event.set()
        playback.finished_event.set()
        self._interrupted_playback_streams.add((playback.device_id, playback.stream_id))
        controller.last_playback_stream_id = playback.stream_id
        controller.last_playback_state = "interrupted"
        controller.last_playback_reason = reason
        if controller.current_playback is playback:
            controller.current_playback = None
            controller.state = "listening"
        else:
            controller.pending_playbacks = [
                pending for pending in controller.pending_playbacks if pending is not playback
            ]

    def _remove_playback_by_intent_locked(
        self,
        *,
        controller: VoiceSessionController,
        intent: PlaybackIntent | None,
        reason: str,
    ) -> tuple[PlaybackStreamContext | None, str | None]:
        """按仲裁器意图移除本地播放流。"""

        if intent is None:
            return None, None
        playback = self._playback_streams.pop((intent.device_id, intent.stream_id), None)
        request_id = self._notification_stream_requests.pop((intent.device_id, intent.stream_id), None)
        if request_id is not None:
            self._notification_request_streams.pop(request_id, None)
        if playback is None:
            return None, request_id
        self._mark_playback_interrupted_locked(controller=controller, playback=playback, reason=reason)
        return playback, request_id

    def _abort_local_playback_streams_after_realtime_interrupt(
        self,
        *,
        device_id: str,
        stream_ids: list[str],
        reason: str,
    ) -> None:
        """实时插话后同步中止本地半双工播放队列。

        主要逻辑：
        1. `RealtimeVoiceRuntime` 已经更新统一播放仲裁器并下发设备中断。
        2. 本方法只负责清理 `VoiceRuntime` 内部仍可能存在的 HTTP 播放流。
        3. 如果目标流不是半双工本地播放流，则静默跳过。
        """

        playbacks: list[PlaybackStreamContext] = []
        cancelled_notification_request_ids: list[str] = []
        with self._lock:
            controller = self._controllers.get(device_id)
            if controller is None:
                return
            for stream_id in stream_ids:
                playback = self._playback_streams.pop((device_id, stream_id), None)
                request_id = self._notification_stream_requests.pop((device_id, stream_id), None)
                if request_id is not None:
                    self._notification_request_streams.pop(request_id, None)
                    cancelled_notification_request_ids.append(request_id)
                if playback is None:
                    continue
                self._mark_playback_interrupted_locked(controller=controller, playback=playback, reason=reason)
                playbacks.append(playback)
        for playback in playbacks:
            try:
                playback.queue.put_nowait(None)
            except queue.Full:
                pass
        for request_id in cancelled_notification_request_ids:
            self._notification_coordinator.cancel_request(
                device_id=device_id,
                request_id=request_id,
                reason=reason,
                clear_queue=False,
            )

    def _wait_for_playback(self, *, device_id: str, stream_id: str, timeout_s: float) -> PlaybackStreamContext:
        deadline = time.monotonic() + timeout_s
        with self._playback_condition:
            while True:
                playback = self._playback_streams.get((device_id, stream_id))
                if playback is not None:
                    return playback
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise build_error(
                        ErrorCode.TIMEOUT,
                        "等待播放流超时",
                        details={"device_id": device_id, "stream_id": stream_id},
                    )
                self._playback_condition.wait(timeout=remaining)

    def _store_asset(self, session_id: str, kind: str, filename: str, data: bytes) -> str:
        directory = os.path.join(self._settings.voice_runs_root, session_id, "audio", kind)
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, filename)
        with open(path, "wb") as file:
            file.write(data)
        return path

    def _store_artifact(self, session_id: str, kind: str, filename: str, data: dict[str, Any]) -> str:
        """落盘派生结果文件。

        参数：
        1. `session_id`：会话编号。
        2. `kind`：派生结果分类目录。
        3. `filename`：文件名。
        4. `data`：待写入的字典数据。

        返回值：
        1. 结果文件绝对或相对路径。
        """

        directory = os.path.join(self._settings.voice_runs_root, session_id, "artifact", kind)
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, filename)
        with open(path, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        return path

    @staticmethod
    def _send_chunked_headers(handler) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", "audio/wav")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Transfer-Encoding", "chunked")
        handler.send_header("Connection", "close")
        handler.end_headers()

    @staticmethod
    def _write_chunk(handler, payload: bytes) -> None:
        if not payload:
            return
        handler.wfile.write(f"{len(payload):X}\r\n".encode("ascii"))
        handler.wfile.write(payload)
        handler.wfile.write(b"\r\n")
        handler.wfile.flush()

    @staticmethod
    def _finish_chunked(handler) -> None:
        handler.wfile.write(b"0\r\n\r\n")
        handler.wfile.flush()

    def _get_controller(self, device_id: str) -> VoiceSessionController:
        with self._lock:
            controller = self._controllers.get(device_id)
        if controller is None:
            raise build_error(
                ErrorCode.STREAM_NOT_FOUND,
                "未找到对应设备的语音会话控制器",
                details={"device_id": device_id},
            )
        return controller

    @staticmethod
    def _ensure_session_match(controller: VoiceSessionController, session_id: str) -> None:
        if controller.session_id != session_id:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "session_id 与当前语音会话不匹配",
                details={"expected_session_id": controller.session_id, "actual_session_id": session_id},
            )


def build_wav_bytes(pcm_bytes: bytes, sample_rate_hz: int, channels: int = 1) -> bytes:
    """把 PCM16 单声道数据封装为 WAV。"""

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(SERVER_SAMPLE_WIDTH_BYTES)
        wav_file.setframerate(sample_rate_hz)
        wav_file.writeframes(pcm_bytes)
    return buffer.getvalue()


def wav_header_unknown_size(sample_rate_hz: int, channels: int, sample_width_bytes: int = 2) -> bytes:
    """生成适用于 chunked 流的 WAV 头。"""

    byte_rate = sample_rate_hz * channels * sample_width_bytes
    block_align = channels * sample_width_bytes
    data_size = 0x7FFFFFF0
    riff_size = 36 + data_size
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        riff_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        sample_rate_hz,
        byte_rate,
        block_align,
        sample_width_bytes * 8,
        b"data",
        data_size,
    )


def extract_text_delta(content: Any) -> str:
    """从增量 content 字段提取文本。"""

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return ""


def extract_message_text(completion: Any) -> str:
    """从非流式模型返回结果中提取文本。

    参数：
    1. `completion`：OpenAI SDK 返回的完整响应对象。

    返回值：
    1. 提取到的文本；若没有文本则返回空字符串。
    """

    choices = getattr(completion, "choices", None)
    if not isinstance(choices, list) or not choices:
        return ""
    first_choice = choices[0]
    message = getattr(first_choice, "message", None)
    content = getattr(message, "content", None)
    return extract_text_delta(content)


def build_audio_data_url(input_wav: bytes) -> str:
    """把 WAV 字节转成 `data:` URL。

    参数：
    1. `input_wav`：完整 WAV 字节。

    返回值：
    1. `data:audio/wav;base64,...` 格式字符串。
    """

    return "data:audio/wav;base64," + base64.b64encode(input_wav).decode("utf-8")


def read_attr_or_key(value: Any, name: str) -> Any:
    """从对象属性或字典键中读取字段。

    参数：
    1. `value`：待读取对象，可以是普通对象、字典或 `None`。
    2. `name`：字段名。

    返回值：
    1. 读取到的字段值；若不存在则返回 `None`。
    """

    if value is None:
        return None
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)
