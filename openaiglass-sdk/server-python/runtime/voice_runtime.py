"""Phase C 语音会话运行时。"""

from __future__ import annotations

import base64
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
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from agent_core import AgentFacade, AgentTurn, DerivedArtifact, MediaAssetRef
from agent_core.context import generate_id
from backend_task_core import TaskEvent
from infra.config import ServerSettings
from infra.errors import AppError, ErrorCode, build_error
from infra.logging import LogContext, get_logger, log_debug, log_error, log_info
from protocol.media import MediaFrame
from runtime.notifications import NotificationCoordinator, NotificationRequest, NotificationSubmitResult
from runtime.playback_arbiter import PlaybackArbiter, PlaybackIntent
from runtime.realtime_voice import RealtimeModelAdapter, RealtimeVoiceRuntime
from runtime.task_event_bridge import TaskEventBridge

SERVER_SAMPLE_RATE_HZ = 16000
SERVER_CHANNELS = 1
SERVER_SAMPLE_WIDTH_BYTES = 2
MODEL_OUTPUT_SAMPLE_RATE_HZ = 24000
PLAYBACK_QUEUE_MAX = 256
UTTERANCE_PHOTO_CAPTURE_TIMEOUT_MS = 10000


@dataclass(slots=True)
class MessageEntry:
    """最小消息上下文条目。"""

    role: str
    kind: str
    text: str
    asset_refs: list[str] = field(default_factory=list)
    created_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))


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
    payload: bytearray = field(default_factory=bytearray)
    frame_count: int = 0
    last_seq: int | None = None
    streaming_asr_session: "StreamingSpeechRecognitionSession | None" = None

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


@dataclass(slots=True)
class ModelChunk:
    """模型流式结果分片。"""

    text_delta: str = ""
    audio_pcm_bytes: bytes = b""
    sample_rate_hz: int = MODEL_OUTPUT_SAMPLE_RATE_HZ


@dataclass(slots=True)
class ReplySynthesisContext:
    """单条回复的语音合成上下文。

    主要功能：
    1. 保存一次回复对应的播放流和重采样状态。
    2. 把流式 TTS 产出的音频持续写入眼镜播放队列。
    """

    stream_id: str
    playback: PlaybackStreamContext
    output_pcm: bytearray = field(default_factory=bytearray)
    resampler: PCM16StreamResampler | None = None


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
        agent_facade: AgentFacade | None = None,
        realtime_model_adapter: RealtimeModelAdapter | None = None,
    ) -> None:
        self._settings = settings
        self._send_control_message = send_control_message
        self._model_client = model_client or DashscopeVoiceModelClient()
        self._asr_client = asr_client or DashscopeSpeechRecognitionClient()
        self._logger = get_logger("server.voice")
        self._lock = threading.Lock()
        self._playback_condition = threading.Condition(self._lock)
        self._controllers: dict[str, VoiceSessionController] = {}
        self._playback_streams: dict[tuple[str, str], PlaybackStreamContext] = {}
        self._notification_stream_requests: dict[tuple[str, str], str] = {}
        self._notification_request_streams: dict[str, tuple[str, str]] = {}
        self._interrupted_playback_streams: set[tuple[str, str]] = set()
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
        self._realtime_voice_runtime = RealtimeVoiceRuntime(
            playback_arbiter=self._playback_arbiter,
            send_control_message=self._send_control_message,
            model_adapter=realtime_model_adapter,
        )

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
                controller.device_type = device_type
                controller.session_id = session_id
                controller.state = "opened"
                controller.current_segment = None
                controller.current_playback = None
                controller.pending_playbacks.clear()
                controller.last_playback_stream_id = None
                controller.last_playback_state = None
                controller.last_playback_reason = None

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
            )
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
                if segment.streaming_asr_session is not None:
                    segment.streaming_asr_session.append_audio(frame.payload)
            except AppError as exc:
                log_debug(
                    self._logger,
                    f"丢弃异常音频帧: code={exc.code} message={exc.message}",
                    LogContext(device_id=device_id, session_id=controller.session_id or None),
                )
                return

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
                            f"stream_id={stream_id} bytes={len(item)} "
                            f"play_request_to_http_audio_ms={self._latency_ms(start=playback.first_play_request_at_ms, end=playback.first_http_audio_chunk_at_ms)} "
                            f"tts_audio_to_http_audio_ms={self._latency_ms(start=playback.first_audio_chunk_at_ms, end=playback.first_http_audio_chunk_at_ms)}"
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
                "active_segment_id": controller.current_segment.segment_id if controller.current_segment else None,
                "reply_stream_id": current_playback.stream_id if current_playback else None,
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

    def _start_utterance_photo_capture(self, *, device_id: str, session_id: str, segment: SegmentBuffer) -> None:
        """在语音段结束后启动后台自动抓拍。

        主要逻辑：
        1. 从 AgentFacade 的 ToolRegistry 读取真实相机网关和语音照片缓存。
        2. 只启动后台任务，不等待端侧图片上传完成。
        3. 抓拍失败只写入缓存记录，不能阻塞 ASR 和大模型链路。

        参数：
        1. `device_id`：当前眼镜设备编号。
        2. `session_id`：当前控制会话编号。
        3. `segment`：刚结束上传的语音段。

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
            )
            log_debug(
                self._logger,
                (
                    "已启动语音结束自动抓拍 "
                    f"segment_id={segment.segment_id} input_stream_id={segment.stream_id} "
                    f"timeout_ms={UTTERANCE_PHOTO_CAPTURE_TIMEOUT_MS}"
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

    def _run_model_pipeline(self, device_id: str, session_id: str, segment: SegmentBuffer) -> None:
        controller = self._get_controller(device_id)
        input_wav = segment.to_wav_bytes()
        input_path = self._store_asset(session_id, "input", f"{segment.segment_id}.wav", input_wav)
        transcript_path = ""
        output_pcm = bytearray()
        playback_stream_id = f"reply_{uuid.uuid4().hex[:12]}"

        try:
            log_info(
                self._logger,
                (
                    "语音链路开始处理音频段 "
                    f"input_stream_id={segment.stream_id} segment_id={segment.segment_id} "
                    f"duration_ms={segment.duration_ms()} bytes={len(input_wav)} "
                    f"asr_model={self._settings.voice_asr_model_name} agent_model={self._settings.agent_model_name}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )
            self._start_utterance_photo_capture(device_id=device_id, session_id=session_id, segment=segment)
            user_text = self._transcribe_segment(
                device_id=device_id,
                session_id=session_id,
                segment=segment,
                input_wav=input_wav,
            ).strip()
            if not user_text:
                raise build_error(
                    ErrorCode.INTERNAL_ERROR,
                    "当前轮用户语音转写结果为空，无法继续调用对话模型",
                    details={"segment_id": segment.segment_id},
                )
            log_debug(
                self._logger,
                f"ASR 转写结果: {user_text}",
                LogContext(device_id=device_id, session_id=session_id),
            )
            log_info(
                self._logger,
                (
                    "ASR 完成，准备进入 agent-core "
                    f"input_stream_id={segment.stream_id} segment_id={segment.segment_id} "
                    f"agent_model={self._settings.agent_model_name} text_length={len(user_text)}"
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
                },
            )
            turn = AgentTurn(
                turn_id=generate_id("turn"),
                session_id=session_id,
                device_id=device_id,
                source="voice_asr",
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
                        artifact_type="asr_transcript",
                        storage_uri=transcript_path,
                        text=user_text,
                        meta={
                            "segment_id": segment.segment_id,
                            "stream_id": segment.stream_id,
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
            model_request_started_at_ms = self._now_ms()
            first_model_token_logged = False

            def _create_final_tts_session() -> StreamingTtsSession:
                """创建当前 Agent 最终回复使用的流式 TTS 会话。

                主要逻辑：
                1. 复用已经提前创建的 `final_synthesis_context`。
                2. 把 TTS 音频回调接到当前播放流。
                3. 该函数也用于预热 session 失效后的重建。

                返回值：
                1. 当前回复可用的 `StreamingTtsSession`。
                """

                assert final_synthesis_context is not None
                return self._model_client.create_streaming_tts_session(
                    settings=self._settings,
                    on_chunk=lambda chunk: self._emit_synthesis_chunk(
                        device_id=device_id,
                        session_id=session_id,
                        context=final_synthesis_context,
                        chunk=chunk,
                    ),
                )

            def _push_text_to_final_tts(text_delta: str) -> None:
                """把文本推给当前预热 TTS，会话失效时重建一次。"""

                nonlocal final_tts_session
                assert final_synthesis_context is not None
                assert final_tts_session is not None
                self._mark_first_text_delta(final_synthesis_context.playback)
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
                threading.Thread(
                    target=self._play_intermediate_reply,
                    args=(device_id, session_id, progress_text),
                    daemon=True,
                ).start()

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

            final_synthesis_context = self._open_reply_synthesis_context(
                device_id=device_id,
                session_id=session_id,
            )
            final_tts_session = _create_final_tts_session()
            log_info(
                self._logger,
                (
                    "TTS 预热已启动 "
                    f"stream_id={final_synthesis_context.stream_id} "
                    f"before_agent_request_ms={max(self._now_ms() - model_request_started_at_ms, 0)}"
                ),
                LogContext(device_id=device_id, session_id=session_id, message_id=turn.turn_id),
            )

            agent_result = self._agent_facade.handle_turn(
                turn,
                progress_callback=_handle_progress_text,
                reply_text_delta_callback=_handle_reply_text_delta,
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

            if final_tts_session is not None and final_synthesis_context is not None:
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
            context = self._open_reply_synthesis_context(device_id=device_id, session_id=session_id)
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
        )
        return ReplySynthesisContext(stream_id=stream_id, playback=playback)

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
                    f"stream_id={playback.stream_id} "
                    f"text_to_play_request_ms={self._latency_ms(start=playback.first_text_delta_at_ms, end=playback.first_play_request_at_ms)} "
                    f"tts_audio_to_play_request_ms={self._latency_ms(start=playback.first_audio_chunk_at_ms, end=playback.first_play_request_at_ms)}"
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
        """把 TTS 音频分片推入当前播放流。"""

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
                    "TTS 返回首段音频 "
                    f"stream_id={context.stream_id} input_sample_rate_hz={chunk.sample_rate_hz} "
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
    ) -> PlaybackStreamContext:
        intent_id = f"{source}:{stream_id}"
        playback = PlaybackStreamContext(
            device_id=device_id,
            session_id=session_id,
            stream_id=stream_id,
            sample_rate=SERVER_SAMPLE_RATE_HZ,
            channels=SERVER_CHANNELS,
            source=source,
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
