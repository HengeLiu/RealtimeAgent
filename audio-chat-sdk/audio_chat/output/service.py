from __future__ import annotations

import os
import queue
import time
import audioop
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from audio_chat.observability import RunRecorder
from audio_chat.protocol import SERVER_PRODUCER_ID, StreamChunk, StreamFormat, new_id
from audio_chat.stream import StreamService

PRIORITY_ORDER = {"low": 0, "normal": 1, "high": 2, "critical": 3}


@dataclass(frozen=True)
class OutputItem:
    """Output Service 内部输出项。

    主要功能：表示一条已经进入输出链路、等待 TTS、播放仲裁或端侧播放的内容。
    主要属性：`priority`、`on_interrupted`、`on_blocked` 和 `ttl_seconds` 只服务
    Output Service 内部调度，不作为 Tool / Task 公开协议对象。
    """

    user_id: str
    session_id: str
    source: str = "agent_reply"
    priority: str = "normal"
    on_interrupted: str | None = None
    on_blocked: str | None = None
    ttl_seconds: int = 0
    dedupe_key: str | None = None
    cached_prompt_key: str | None = None


@dataclass(frozen=True)
class AssistantTextDelta:
    user_id: str
    session_id: str
    text: str
    final: bool = False
    intent: OutputItem | None = None


@dataclass(frozen=True)
class PlaybackDecision:
    action: str
    reason: str
    active_stream_id: str | None = None
    interrupted_stream_id: str | None = None
    queued_intent_id: str | None = None
    user_id: str | None = None
    session_id: str | None = None
    priority: str | None = None
    created_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class NotificationRequest:
    """通知请求。

    主要功能：Output Service 内部接收 TaskEvent、系统提醒或业务通知后的统一入口对象。
    主要属性：`text` 为可播报内容，`priority`、`ttl_seconds`、`dedupe_key` 参与通知决策。
    """

    user_id: str
    session_id: str
    text: str
    priority: str = "normal"
    ttl_seconds: int = 0
    dedupe_key: str | None = None
    merge_key: str | None = None
    merge_window_seconds: float = 1.0
    allow_direct_notify: bool = True
    requires_agent_context_sync: bool = False
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class NotificationDecision:
    """通知决策。

    主要功能：记录通知协调层是否放行、合并或丢弃通知。
    """

    action: str
    reason: str
    dedupe_key: str | None = None
    merge_key: str | None = None
    requires_agent_context_sync: bool = False


class NotificationCoordinator:
    """通知协调器。

    主要功能：接收 TaskEvent 和系统通知，做最小去重决策后进入 Output Router。
    """

    def __init__(self, *, output_service: "OutputService | None" = None) -> None:
        self.output_service = output_service
        self._seen_dedupe_keys: set[str] = set()
        self._pending_merge: dict[str, tuple[float, NotificationRequest]] = {}
        self._decisions: list[NotificationDecision] = []

    def submit(self, request: NotificationRequest) -> NotificationDecision:
        """提交通知。

        主要逻辑：相同 dedupe_key 只放行一次；放行后转为 Output Service 文本提交。
        参数：`request` 为通知请求。
        返回值：通知决策。
        异常情况：下游输出失败时向上抛出。
        """
        if request.dedupe_key and request.dedupe_key in self._seen_dedupe_keys:
            decision = NotificationDecision(
                action="drop",
                reason="dedupe",
                dedupe_key=request.dedupe_key,
                merge_key=request.merge_key,
                requires_agent_context_sync=request.requires_agent_context_sync,
            )
            self._record_decision(decision)
            return decision
        if request.dedupe_key:
            self._seen_dedupe_keys.add(request.dedupe_key)
        if request.merge_key:
            now = time.time()
            existing = self._pending_merge.get(request.merge_key)
            if existing is not None and now - existing[0] <= request.merge_window_seconds:
                previous = existing[1]
                merged = NotificationRequest(
                    user_id=request.user_id,
                    session_id=request.session_id,
                    text=f"{previous.text}\n{request.text}".strip(),
                    priority=_higher_priority(previous.priority, request.priority),
                    ttl_seconds=max(previous.ttl_seconds, request.ttl_seconds),
                    dedupe_key=request.dedupe_key,
                    merge_key=request.merge_key,
                    merge_window_seconds=request.merge_window_seconds,
                    allow_direct_notify=request.allow_direct_notify and previous.allow_direct_notify,
                    requires_agent_context_sync=(
                        request.requires_agent_context_sync or previous.requires_agent_context_sync
                    ),
                    metadata={**previous.metadata, **request.metadata, "merged": True},
                )
                self._pending_merge[request.merge_key] = (now, merged)
                decision = NotificationDecision(
                    action="merge",
                    reason="merge_window",
                    dedupe_key=request.dedupe_key,
                    merge_key=request.merge_key,
                    requires_agent_context_sync=merged.requires_agent_context_sync,
                )
                self._record_decision(decision)
                return decision
            self._pending_merge[request.merge_key] = (now, request)
        if not request.allow_direct_notify:
            decision = NotificationDecision(
                action="hold",
                reason="direct_notify_disabled",
                dedupe_key=request.dedupe_key,
                merge_key=request.merge_key,
                requires_agent_context_sync=request.requires_agent_context_sync,
            )
            self._record_decision(decision)
            return decision
        if self.output_service is not None and request.text:
            self.output_service.submit_text(
                user_id=request.user_id,
                session_id=request.session_id,
                text=request.text,
                priority=request.priority,
                ttl_seconds=request.ttl_seconds,
            )
        decision = NotificationDecision(
            action="route",
            reason="accepted",
            dedupe_key=request.dedupe_key,
            merge_key=request.merge_key,
            requires_agent_context_sync=request.requires_agent_context_sync,
        )
        self._record_decision(decision)
        return decision

    def flush_merge(self, merge_key: str) -> NotificationDecision | None:
        """提交一条合并后的通知。"""

        item = self._pending_merge.pop(merge_key, None)
        if item is None:
            return None
        _created_at, request = item
        if self.output_service is not None and request.text and request.allow_direct_notify:
            self.output_service.submit_text(
                user_id=request.user_id,
                session_id=request.session_id,
                text=request.text,
                priority=request.priority,
                ttl_seconds=request.ttl_seconds,
            )
        decision = NotificationDecision(
            action="route" if request.allow_direct_notify else "hold",
            reason="merged_flush" if request.allow_direct_notify else "direct_notify_disabled",
            dedupe_key=request.dedupe_key,
            merge_key=request.merge_key,
            requires_agent_context_sync=request.requires_agent_context_sync,
        )
        self._record_decision(decision)
        return decision

    def recent_decisions(self, limit: int = 20) -> list[dict]:
        """返回最近通知决策快照。"""

        return [decision.__dict__ for decision in self._decisions[-limit:]]

    def _record_decision(self, decision: NotificationDecision) -> None:
        self._decisions.append(decision)
        if len(self._decisions) > 100:
            self._decisions = self._decisions[-100:]


def _higher_priority(left: str, right: str) -> str:
    """返回两种优先级中更高的一项。"""

    return left if PRIORITY_ORDER.get(left, 0) >= PRIORITY_ORDER.get(right, 0) else right


@dataclass(frozen=True)
class TtsProviderConfig:
    provider: str = "mock"
    model: str = "mock-tts"
    voice: str = "mock"
    streaming: bool = True
    allow_mock_fallback: bool = True
    websocket_api_url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
    sample_rate_hz: int = 22050
    request_timeout_seconds: float = 5.0
    max_retries: int = 1


class StreamingTTS(Protocol):
    provider_name: str
    model: str
    streaming: bool

    def synthesize_delta(self, text: str) -> bytes:
        ...

    def metrics(self) -> dict:
        ...

    def finish(self) -> None:
        ...


class MockStreamingTTS:
    """本地调试用流式 TTS。

    主要功能：在没有真实 TTS provider 时，为端到端链路生成可听见的 PCM16 诊断音。
    主要方法：`synthesize_delta` 将文本片段转成短音频，`metrics` 返回首包延迟指标。
    主要属性：`sample_rate_hz` 决定输出采样率，`_text_push_count` 用于让连续片段音高略有变化。
    """

    provider_name = "mock"
    streaming = True

    def __init__(self, model: str = "mock-tts", voice: str = "mock", sample_rate_hz: int = 16000) -> None:
        self.model = model
        self.voice = voice
        self.sample_rate_hz = sample_rate_hz
        self._first_text_at: float | None = None
        self._first_audio_at: float | None = None
        self._text_push_count = 0
        self._text_chars = 0

    def synthesize_delta(self, text: str) -> bytes:
        """把文本 delta 合成为可听见的 PCM16 音频。

        主要逻辑：mock provider 不生成真实语音，只生成带淡入淡出的短正弦音，
        用于确认 server 到端侧播放器的输出链路确实可用。
        参数：`text` 为模型输出的文本片段。
        返回值：小端 PCM16 单声道字节流。
        异常情况：空文本返回空字节，不抛出异常。
        """

        if not text:
            return b""
        now = time.time()
        if self._first_text_at is None:
            self._first_text_at = now
        self._text_push_count += 1
        self._text_chars += len(text)
        duration_seconds = max(0.12, min(0.25, len(text.encode("utf-8")) * 0.012))
        samples = min(4000, max(1600, int(self.sample_rate_hz * duration_seconds)))
        frequency_hz = 440 + (self._text_push_count % 4) * 80
        fade_samples = max(1, min(samples // 3, int(self.sample_rate_hz * 0.025)))
        amplitude = 0.22
        pcm = bytearray(samples * 2)
        for index in range(samples):
            fade_in = min(1.0, index / fade_samples)
            fade_out = min(1.0, (samples - index - 1) / fade_samples)
            envelope = max(0.0, min(fade_in, fade_out))
            value = int(math.sin(2 * math.pi * frequency_hz * index / self.sample_rate_hz) * amplitude * envelope * 32767)
            pcm[index * 2 : index * 2 + 2] = value.to_bytes(2, "little", signed=True)
        if self._first_audio_at is None:
            self._first_audio_at = time.time()
        return bytes(pcm)

    def synthesize_text(self, text: str) -> bytes:
        """一次性合成完整文本。

        主要逻辑：mock provider 没有真实语音合成能力，因此复用 `synthesize_delta()` 生成
        一段可听诊断音。参数 `text` 为完整播报文本；返回值为 PCM16 音频。
        异常情况：空文本返回空字节。
        """

        return self.synthesize_delta(text)

    def metrics(self) -> dict:
        first_chunk_latency_ms = None
        if self._first_text_at is not None and self._first_audio_at is not None:
            first_chunk_latency_ms = int((self._first_audio_at - self._first_text_at) * 1000)
        return {
            "provider": self.provider_name,
            "model": self.model,
            "voice": self.voice,
            "sample_rate_hz": self.sample_rate_hz,
            "first_text_at": self._first_text_at,
            "first_audio_at": self._first_audio_at,
            "first_chunk_latency_ms": first_chunk_latency_ms,
            "tts_first_audio_latency_ms": first_chunk_latency_ms,
            "text_push_count": self._text_push_count,
            "text_chars": self._text_chars,
            "mock": True,
        }

    def finish(self) -> None:
        return None


class DashScopeStreamingTTS:
    provider_name = "dashscope"
    streaming = True

    def __init__(
        self,
        model: str,
        voice: str,
        *,
        websocket_api_url: str,
        sample_rate_hz: int,
        request_timeout_seconds: float = 5.0,
        max_retries: int = 1,
    ) -> None:
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            raise RuntimeError("DASHSCOPE_API_KEY is not set; TTS provider downgraded to mock")
        try:
            import dashscope
            from dashscope.audio.tts_v2 import AudioFormat, ResultCallback, SpeechSynthesizer
        except ImportError as exc:
            raise RuntimeError("dashscope package is not installed; TTS provider downgraded to mock") from exc

        self.model = model
        self.voice = voice
        self.sample_rate_hz = sample_rate_hz
        self.request_timeout_seconds = request_timeout_seconds
        self.max_retries = max_retries
        self._audio: queue.Queue[bytes] = queue.Queue()
        self._created_at = time.time()
        self._first_text_at: float | None = None
        self._first_audio_at: float | None = None
        self._text_push_count = 0
        self._text_chars = 0

        sink = self
        dashscope.api_key = api_key
        dashscope.base_websocket_api_url = websocket_api_url

        class _Callback(ResultCallback):
            def on_data(self, data: bytes) -> None:  # pragma: no cover - exercised in integration
                if data:
                    if sink._first_audio_at is None:
                        sink._first_audio_at = time.time()
                    sink._audio.put(sink._normalize_sample_rate(bytes(data)))

            def on_error(self, message: str):  # pragma: no cover - exercised in integration
                sink._audio.put(b"")

        attr = f"PCM_{sample_rate_hz}HZ_MONO_16BIT"
        fmt = getattr(AudioFormat, attr, AudioFormat.PCM_22050HZ_MONO_16BIT)
        self._source_sample_rate_hz = sample_rate_hz if hasattr(AudioFormat, attr) else 22050
        self._speech_synthesizer_cls = SpeechSynthesizer
        self._audio_format = fmt
        self._synthesizer = SpeechSynthesizer(model=model, voice=voice, format=fmt, callback=_Callback())

    def synthesize_delta(self, text: str) -> bytes:
        if not text:
            return b""
        if self._first_text_at is None:
            self._first_text_at = time.time()
        self._text_push_count += 1
        self._text_chars += len(text)
        self._synthesizer.streaming_call(text)
        deadline = time.time() + max(0.1, self.request_timeout_seconds)
        chunks: list[bytes] = []
        while time.time() < deadline:
            try:
                chunk = self._audio.get(timeout=0.05)
            except queue.Empty:
                if chunks:
                    break
                continue
            if chunk:
                chunks.append(chunk)
                break
        return b"".join(chunks)

    def synthesize_text(self, text: str) -> bytes:
        """一次性合成完整文本。

        主要逻辑：
        1. Tool / Task 通知这类输出已经拿到完整文本，不需要走增量 TTS。
        2. 使用 DashScope `SpeechSynthesizer.call()` 的同步语音合成路径，避免先打开
           output stream 后再等待首包，导致端侧收到空流。
        3. 对 provider 返回的 PCM 做采样率归一化，保持和 stream format 一致。

        参数：`text` 为完整播报文本。
        返回值：PCM16 单声道音频字节。
        异常情况：DashScope 调用失败或超时时向上抛出，让调用方记录明确错误。
        """

        if not text:
            return b""
        now = time.time()
        if self._first_text_at is None:
            self._first_text_at = now
        self._text_push_count += 1
        self._text_chars += len(text)
        synthesizer = self._speech_synthesizer_cls(model=self.model, voice=self.voice, format=self._audio_format)
        timeout_millis = int(max(self.request_timeout_seconds, 15.0) * 1000)
        payload = synthesizer.call(text, timeout_millis=timeout_millis)
        if payload and self._first_audio_at is None:
            self._first_audio_at = time.time()
        return self._normalize_sample_rate(bytes(payload or b""))

    def metrics(self) -> dict:
        first_audio_latency_ms = None
        if self._first_text_at is not None and self._first_audio_at is not None:
            first_audio_latency_ms = int((self._first_audio_at - self._first_text_at) * 1000)
        return {
            "provider": self.provider_name,
            "model": self.model,
            "voice": self.voice,
            "sample_rate_hz": self.sample_rate_hz,
            "source_sample_rate_hz": self._source_sample_rate_hz,
            "endpoint": "dashscope.tts_v2.websocket",
            "timeout_seconds": self.request_timeout_seconds,
            "max_retries": self.max_retries,
            "fallback_policy": "fail_after_provider_created",
            "first_text_at": self._first_text_at,
            "first_audio_at": self._first_audio_at,
            "first_chunk_latency_ms": first_audio_latency_ms,
            "tts_first_audio_latency_ms": first_audio_latency_ms,
            "text_push_count": self._text_push_count,
            "text_chars": self._text_chars,
        }

    def finish(self) -> None:
        self._synthesizer.streaming_complete()

    def _normalize_sample_rate(self, pcm: bytes) -> bytes:
        if self._source_sample_rate_hz == self.sample_rate_hz:
            return pcm
        converted, _state = audioop.ratecv(pcm, 2, 1, self._source_sample_rate_hz, self.sample_rate_hz, None)
        return converted


def build_tts_provider(config: TtsProviderConfig) -> tuple[StreamingTTS, str | None]:
    try:
        if config.provider == "mock":
            return MockStreamingTTS(model=config.model, voice=config.voice, sample_rate_hz=config.sample_rate_hz), None
        if config.provider == "dashscope":
            return DashScopeStreamingTTS(
                model=config.model,
                voice=config.voice,
                websocket_api_url=config.websocket_api_url,
                sample_rate_hz=config.sample_rate_hz,
                request_timeout_seconds=config.request_timeout_seconds,
                max_retries=config.max_retries,
            ), None
        raise RuntimeError(f"unsupported TTS provider: {config.provider}")
    except RuntimeError as exc:
        if not config.allow_mock_fallback:
            raise
        return MockStreamingTTS(sample_rate_hz=config.sample_rate_hz), str(exc)


@dataclass
class StreamingTtsOutputSource:
    """基于 Streaming TTS 的输出源。

    主要功能：持续接收 assistant_text.delta，轮到播放时把累积文本送入 TTS 生成音频。
    主要属性：`tts` 为当前 output 独立 TTS session，`_pending_text` 为尚未合成的文本。
    """

    tts: StreamingTTS
    _pending_text: str = ""
    final_requested: bool = False

    def append_text(self, text: str) -> None:
        """追加文本 delta。

        主要逻辑：排队期间只累积文本，播放期间由 router 调用 `synthesize_pending()` 消费。
        参数：`text` 为模型增量文本。
        返回值：无。
        异常情况：无。
        """
        self._pending_text += text

    def mark_final(self) -> None:
        self.final_requested = True

    def stream_format(self) -> StreamFormat:
        metrics = self.tts.metrics()
        return StreamFormat(
            codec="pcm16le",
            sample_rate=int(metrics.get("sample_rate_hz") or 16000),
            channels=1,
            chunk_ms=40,
        )

    def synthesize_pending(self) -> bytes:
        text = self._pending_text
        self._pending_text = ""
        return self.tts.synthesize_delta(text)

    def metrics(self) -> dict:
        return self.tts.metrics()

    def finish(self) -> None:
        self.tts.finish()


@dataclass
class NativeAudioOutputSource:
    """原生音频输出源。

    主要功能：保存 Omni / realtime provider 直接输出的 audio delta，不经过 TTS。
    主要属性：`format` 描述原生音频格式，`chunks` 保存待播放音频。
    """

    format: StreamFormat
    metadata: dict = field(default_factory=dict)
    chunks: list[bytes] = field(default_factory=list)
    final_requested: bool = False

    def append_text(self, text: str) -> None:
        return None

    def mark_final(self) -> None:
        self.final_requested = True

    def stream_format(self) -> StreamFormat:
        return self.format

    def synthesize_pending(self) -> bytes:
        if not self.chunks:
            return b""
        return self.chunks.pop(0)

    def metrics(self) -> dict:
        return {"provider": "native_audio", "sample_rate_hz": self.format.sample_rate, **self.metadata}

    def finish(self) -> None:
        return None

    def chunk_bytes(self) -> int:
        """计算当前格式下每个 stream chunk 的字节数。

        主要逻辑：当前只支持 pcm16le，按采样率、通道数和 chunk_ms 计算单片大小。
        参数：无。
        返回值：每个音频 chunk 的字节数，至少为 2 字节。
        异常情况：无。
        """
        if self.format.codec != "pcm16le":
            return max(1, self.format.sample_rate * self.format.channels * self.format.chunk_ms // 1000)
        return max(2, self.format.sample_rate * self.format.channels * self.format.chunk_ms // 1000 * 2)


@dataclass
class CachedAudioOutputSource:
    """缓存提示音输出源。"""

    cache_key: str
    audio: bytes
    format: StreamFormat
    metadata: dict = field(default_factory=dict)
    final_requested: bool = True

    def append_text(self, text: str) -> None:
        return None

    def mark_final(self) -> None:
        self.final_requested = True

    def stream_format(self) -> StreamFormat:
        return self.format

    def synthesize_pending(self) -> bytes:
        payload = self.audio
        self.audio = b""
        return payload

    def metrics(self) -> dict:
        return {"provider": "cached_prompt_audio", "cache_key": self.cache_key, **self.metadata}

    def finish(self) -> None:
        return None


@dataclass
class QueuedOutput:
    source: StreamingTtsOutputSource | NativeAudioOutputSource | CachedAudioOutputSource
    intent: OutputItem
    created_at: float = field(default_factory=time.time)
    source_stream_id: str | None = None


class PlaybackArbiter:
    def __init__(self, *, stream_service: StreamService, recorder: RunRecorder, max_queue_size: int = 32) -> None:
        self.stream_service = stream_service
        self.recorder = recorder
        self.max_queue_size = max_queue_size
        self._active_by_user: dict[
            str,
            tuple[OutputItem, str, StreamingTtsOutputSource | NativeAudioOutputSource | CachedAudioOutputSource],
        ] = {}
        self._queue_by_user: dict[str, list[QueuedOutput]] = {}
        self._recent_decisions: list[PlaybackDecision] = []

    def submit(
        self,
        *,
        source: StreamingTtsOutputSource | NativeAudioOutputSource | CachedAudioOutputSource,
        intent: OutputItem,
        format: StreamFormat | None = None,
    ) -> tuple[PlaybackDecision, str | None]:
        active = self._active_by_user.get(intent.user_id)
        if active is None:
            stream_id = self._open_output_stream(intent, source=source, format=format)
            decision = PlaybackDecision(action="play_now", reason="no_active_playback", active_stream_id=stream_id, user_id=intent.user_id, session_id=intent.session_id, priority=intent.priority)
            self._record(intent.session_id, decision)
            return decision, stream_id
        active_intent, active_stream_id, active_source = active
        if PRIORITY_ORDER[intent.priority] > PRIORITY_ORDER[active_intent.priority]:
            self.stream_service.cancel_stream(active_stream_id, reason="interrupted_by_higher_priority")
            if active_intent.on_interrupted == "requeue":
                self._queue_by_user.setdefault(intent.user_id, []).append(
                    QueuedOutput(source=active_source, intent=active_intent, source_stream_id=active_stream_id)
                )
            stream_id = self._open_output_stream(intent, source=source, format=format)
            decision = PlaybackDecision(
                action="interrupt",
                reason="higher_priority",
                active_stream_id=stream_id,
                interrupted_stream_id=active_stream_id,
                user_id=intent.user_id,
                session_id=intent.session_id,
                priority=intent.priority,
            )
            self._record(intent.session_id, decision)
            return decision, stream_id
        if intent.on_blocked == "queue":
            queue = self._queue_by_user.setdefault(intent.user_id, [])
            if len(queue) >= self.max_queue_size:
                decision = PlaybackDecision(action="drop", reason="queue_full", user_id=intent.user_id, session_id=intent.session_id, priority=intent.priority)
                self._record(intent.session_id, decision)
                return decision, None
            queue.append(QueuedOutput(source=source, intent=intent))
            decision = PlaybackDecision(action="queue", reason="active_playback_not_preempted", queued_intent_id=intent.dedupe_key or intent.session_id, user_id=intent.user_id, session_id=intent.session_id, priority=intent.priority)
            self._record(intent.session_id, decision)
            return decision, None
        decision = PlaybackDecision(action="drop", reason="active_playback_not_preempted", user_id=intent.user_id, session_id=intent.session_id, priority=intent.priority)
        self._record(intent.session_id, decision)
        return decision, None

    def on_playback_finished(self, user_id: str, stream_id: str) -> QueuedOutput | None:
        active = self._active_by_user.get(user_id)
        if active and active[1] == stream_id:
            self._active_by_user.pop(user_id, None)
            return self.pop_next(user_id)
        return None

    def cancel_current(self, user_id: str, *, session_id: str | None, reason: str) -> PlaybackDecision:
        active = self._active_by_user.pop(user_id, None)
        if active is None:
            decision = PlaybackDecision(action="cancel_current", reason="no_active_playback", user_id=user_id, session_id=session_id)
            self._record(session_id or "interruptions", decision)
            return decision
        _intent, stream_id, _source = active
        self.stream_service.cancel_stream(stream_id, reason=reason)
        decision = PlaybackDecision(action="cancel_current", reason=reason, interrupted_stream_id=stream_id, user_id=user_id, session_id=session_id, priority=_intent.priority)
        self._record(session_id or "interruptions", decision)
        return decision

    def pop_next(self, user_id: str) -> QueuedOutput | None:
        queue = self._queue_by_user.get(user_id, [])
        while queue:
            queued = queue.pop(0)
            if queued.intent.ttl_seconds and time.time() - queued.created_at > queued.intent.ttl_seconds:
                self._record(queued.intent.session_id, PlaybackDecision(action="drop", reason="ttl_expired", user_id=queued.intent.user_id, session_id=queued.intent.session_id, priority=queued.intent.priority))
                continue
            return queued
        return None

    def activate_queued(self, queued: QueuedOutput, *, format: StreamFormat | None = None) -> str:
        stream_id = self._open_output_stream(queued.intent, source=queued.source, format=format)
        self._record(
            queued.intent.session_id,
            PlaybackDecision(action="play_now", reason="queued_playback_ready", active_stream_id=stream_id, user_id=queued.intent.user_id, session_id=queued.intent.session_id, priority=queued.intent.priority),
        )
        return stream_id

    def _open_output_stream(
        self,
        intent: OutputItem,
        *,
        source: StreamingTtsOutputSource | NativeAudioOutputSource | CachedAudioOutputSource,
        format: StreamFormat | None = None,
    ) -> str:
        handle = self.stream_service.open_stream(
            user_id=intent.user_id,
            session_id=intent.session_id,
            stream_type="actuator.speaker",
            producer_id=SERVER_PRODUCER_ID,
            format=format or StreamFormat(codec="pcm16le", sample_rate=16000, channels=1, chunk_ms=40),
            stream_id=new_id("stream_out"),
        )
        self._active_by_user[intent.user_id] = (intent, handle.stream_id, source)
        return handle.stream_id

    def _record(self, session_id: str, decision: PlaybackDecision) -> None:
        self._recent_decisions.append(decision)
        if len(self._recent_decisions) > 100:
            self._recent_decisions = self._recent_decisions[-100:]
        self.recorder.record_playback_decision(session_id, decision.__dict__)

    def debug_snapshot(self, *, recent_limit: int = 20) -> dict:
        """返回播放仲裁调试快照。"""

        snapshot = {
            "active": {
                user_id: {
                    "session_id": intent.session_id,
                    "stream_id": stream_id,
                    "priority": intent.priority,
                    "source": intent.source,
                    "on_interrupted": intent.on_interrupted,
                    "on_blocked": intent.on_blocked,
                }
                for user_id, (intent, stream_id, _source) in self._active_by_user.items()
            },
            "queued": {
                user_id: [
                    {
                        "session_id": queued.intent.session_id,
                        "priority": queued.intent.priority,
                        "source": queued.intent.source,
                        "ttl_seconds": queued.intent.ttl_seconds,
                        "age_ms": int((time.time() - queued.created_at) * 1000),
                    }
                    for queued in queue
                ]
                for user_id, queue in self._queue_by_user.items()
            },
            "recent_decisions": [decision.__dict__ for decision in self._recent_decisions[-recent_limit:]],
        }
        self.recorder.write_playback_snapshot(snapshot)
        return snapshot


class OutputRouter:
    def __init__(
        self,
        *,
        stream_service: StreamService,
        recorder: RunRecorder,
        tts_config: TtsProviderConfig | None = None,
        tts: StreamingTTS | None = None,
        default_priority: str = "normal",
        default_on_blocked: str = "queue",
        default_on_interrupted: str = "drop",
        max_queue_size: int = 32,
        tool_progress_audio_mode: str = "cached",
        tool_progress_priority: str = "low",
        tool_progress_ttl_seconds: int = 10,
    ) -> None:
        self.stream_service = stream_service
        self.recorder = recorder
        self.tts_config = tts_config or TtsProviderConfig()
        self._injected_tts = tts
        self.default_priority = default_priority
        self.default_on_blocked = default_on_blocked
        self.default_on_interrupted = default_on_interrupted
        self.tool_progress_audio_mode = tool_progress_audio_mode
        self.tool_progress_priority = tool_progress_priority
        self.tool_progress_ttl_seconds = tool_progress_ttl_seconds
        self.arbiter = PlaybackArbiter(stream_service=stream_service, recorder=recorder, max_queue_size=max_queue_size)
        self._stream_by_session: dict[str, str] = {}
        self._seq_by_stream: dict[str, int] = {}
        self._source_by_session: dict[str, StreamingTtsOutputSource] = {}
        self._source_by_stream: dict[str, StreamingTtsOutputSource | NativeAudioOutputSource | CachedAudioOutputSource] = {}
        self._native_source_by_session: dict[str, NativeAudioOutputSource] = {}
        self._cached_audio_by_key: dict[tuple[str, int, int], bytes] = {}
        self._payload_by_stream: dict[str, bytearray] = {}
        self._queued_sessions: set[str] = set()
        self._finish_listeners: list[Callable[[str, str, str], None]] = []

    def add_finish_listener(self, listener: Callable[[str, str, str], None]) -> None:
        """注册 output stream 完成回调。

        主要逻辑：让 App 能在当前回复播放结束后处理 `close_after_reply`，但不让
        Output Service 直接持有音频会话状态。
        参数：`listener` 接收 user_id、session_id、stream_id。
        返回值：无。
        异常情况：无。
        """

        self._finish_listeners.append(listener)

    def on_agent_text_delta(self, delta: AssistantTextDelta) -> None:
        intent = self._intent_with_defaults(delta.intent or OutputItem(user_id=delta.user_id, session_id=delta.session_id))
        source = self._source_by_session.get(delta.session_id)
        if source is None:
            source = StreamingTtsOutputSource(tts=self._new_tts())
            self._source_by_session[delta.session_id] = source
        if delta.text:
            source.append_text(delta.text)
        if delta.final:
            source.mark_final()
        if delta.session_id in self._queued_sessions:
            return
        stream_id = self._stream_by_session.get(delta.session_id)
        if stream_id is not None and self.stream_service.registry.get(stream_id).state != "open":
            self._stream_by_session.pop(delta.session_id, None)
            stream_id = None
        if stream_id is None:
            decision, stream_id = self.arbiter.submit(source=source, intent=intent, format=source.stream_format())
            if decision.action == "queue":
                self._queued_sessions.add(delta.session_id)
                return
            if decision.action == "drop" or stream_id is None:
                return
            self._stream_by_session[delta.session_id] = stream_id
            self._source_by_stream[stream_id] = source
        payload = source.synthesize_pending()
        seq = self._seq_by_stream.get(stream_id, 0)
        if payload:
            chunk = StreamChunk(
                user_id=delta.user_id,
                session_id=delta.session_id,
                stream_id=stream_id,
                stream_type="actuator.speaker",
                seq=seq,
                payload=payload,
                sample_rate=self.stream_service.registry.get(stream_id).format.sample_rate,
                duration_ms=40,
                final=False,
            )
            self.stream_service.write_chunk(chunk)
            self._payload_by_stream.setdefault(stream_id, bytearray()).extend(payload)
            self.recorder.record_agent_event(
                delta.session_id,
                {
                    "event": "assistant_audio.delta",
                    "stream_id": stream_id,
                    "payload_size": len(payload),
                    "tts": source.metrics(),
                    "stream_format": self.stream_service.registry.get(stream_id).format.__dict__,
                },
            )
            self._seq_by_stream[stream_id] = seq + 1
        if delta.final:
            self._finish_stream(delta.user_id, delta.session_id, stream_id, source)

    def on_assistant_audio_delta(
        self,
        *,
        user_id: str,
        session_id: str,
        audio: bytes,
        format: StreamFormat,
        final: bool = False,
        intent: OutputItem | None = None,
        metadata: dict | None = None,
    ) -> None:
        """处理 provider 原生 audio delta。

        主要逻辑：首包到达时通过 Playback Arbiter 打开 actuator.speaker stream；
        后续 audio delta 写入同一 stream；final=True 时关闭 stream。
        参数：`audio` 为 provider PCM payload，`format` 为 provider 输出格式。
        返回值：无。
        异常情况：stream 服务写入失败时抛出异常。
        """
        resolved_intent = self._intent_with_defaults(intent or OutputItem(user_id=user_id, session_id=session_id))
        source = self._native_source_by_session.get(session_id)
        if source is None:
            source = NativeAudioOutputSource(format=format, metadata=dict(metadata or {}))
            self._native_source_by_session[session_id] = source
        elif metadata:
            source.metadata.update(metadata)
        if audio:
            source.chunks.append(audio)
        if final:
            source.mark_final()
        if session_id in self._queued_sessions:
            if final:
                self.recorder.record_agent_event(
                    session_id,
                    {"event": "assistant_audio.done", "native_audio": source.metrics(), "queued": True},
                )
            return
        stream_id = self._stream_by_session.get(session_id)
        if stream_id is not None and self.stream_service.registry.get(stream_id).state != "open":
            self._stream_by_session.pop(session_id, None)
            stream_id = None
        if stream_id is None and final and not audio and not source.chunks:
            self.recorder.record_agent_event(
                session_id,
                {
                    "event": "assistant_audio.done",
                    "stream_id": None,
                    "native_audio": source.metrics(),
                    "empty_output": True,
                },
            )
            self._native_source_by_session.pop(session_id, None)
            return
        if stream_id is None and audio:
            decision, stream_id = self.arbiter.submit(source=source, intent=resolved_intent, format=source.stream_format())
            if decision.action == "queue":
                self._queued_sessions.add(session_id)
                return
            if decision.action == "drop" or stream_id is None:
                return
            self._stream_by_session[session_id] = stream_id
            self._source_by_stream[stream_id] = source
        if stream_id is None:
            return
        payload = source.synthesize_pending()
        seq = self._seq_by_stream.get(stream_id, 0)
        if payload:
            handle_format = self.stream_service.registry.get(stream_id).format
            written_bytes = 0
            chunk_count = 0
            for part in self._split_native_audio_payload(payload, source=source):
                chunk = StreamChunk(
                    user_id=user_id,
                    session_id=session_id,
                    stream_id=stream_id,
                    stream_type="actuator.speaker",
                    seq=seq,
                    payload=part,
                    codec=handle_format.codec,
                    sample_rate=handle_format.sample_rate,
                    channels=handle_format.channels,
                    duration_ms=handle_format.chunk_ms,
                    final=False,
                    metadata={"source": "native_audio", **dict(metadata or {})},
                )
                self.stream_service.write_chunk(chunk)
                self._payload_by_stream.setdefault(stream_id, bytearray()).extend(part)
                written_bytes += len(part)
                chunk_count += 1
                seq += 1
            self.recorder.record_agent_event(
                session_id,
                {
                    "event": "assistant_audio.delta",
                    "stream_id": stream_id,
                    "payload_size": written_bytes,
                    "chunk_count": chunk_count,
                    "native_audio": source.metrics(),
                    "stream_format": handle_format.__dict__,
                },
            )
            self._seq_by_stream[stream_id] = seq
        if final:
            self.recorder.record_agent_event(
                session_id,
                {"event": "assistant_audio.done", "stream_id": stream_id, "native_audio": source.metrics()},
            )
            self._finish_stream(user_id, session_id, stream_id, source)
            self._native_source_by_session.pop(session_id, None)

    def _finish_stream(
        self,
        user_id: str,
        session_id: str,
        stream_id: str,
        source: StreamingTtsOutputSource | NativeAudioOutputSource | CachedAudioOutputSource,
    ) -> None:
        source.finish()
        handle = self.stream_service.registry.get(stream_id)
        payload = bytes(self._payload_by_stream.pop(stream_id, bytearray()))
        if payload and handle.format.codec == "pcm16le":
            self.recorder.record_output_wav(
                session_id=session_id,
                stream_id=stream_id,
                pcm=payload,
                sample_rate=handle.format.sample_rate,
                channels=handle.format.channels,
            )
        self.recorder.record_stream_event(
            session_id,
            {
                "event": "stream.output.summary",
                "stream_id": stream_id,
                "stream_type": "actuator.speaker",
                "payload_size": len(payload),
                "source": source.metrics(),
            },
        )
        self.stream_service.close_stream(stream_id, reason="assistant_audio.done")
        self._stream_by_session.pop(session_id, None)
        self._source_by_stream.pop(stream_id, None)
        self._native_source_by_session.pop(session_id, None)
        next_output = self.arbiter.on_playback_finished(user_id, stream_id)
        for listener in list(self._finish_listeners):
            listener(user_id, session_id, stream_id)
        if next_output is not None:
            self._queued_sessions.discard(next_output.intent.session_id)
            self._play_queued_output(next_output)

    def mark_endpoint_playback_finished(self, user_id: str, session_id: str | None, stream_id: str | None) -> None:
        """处理端侧回报的播放完成事件。

        主要逻辑：当端侧发送 `stream.output.finished/closed` 时，释放 PlaybackArbiter
        中可能残留的 active 输出，并在有排队输出时立即接续播放。
        参数：`user_id` 为用户标识；`session_id` 为端侧会话；`stream_id` 为已完成的输出流。
        返回值：无。
        异常情况：`stream_id` 为空或不是当前 active 输出时忽略。
        """

        if not stream_id:
            return
        next_output = self.arbiter.on_playback_finished(user_id, stream_id)
        for stored_session, stored_stream_id in list(self._stream_by_session.items()):
            if stored_stream_id == stream_id:
                self._stream_by_session.pop(stored_session, None)
                self._native_source_by_session.pop(stored_session, None)
                self._queued_sessions.discard(stored_session)
        self._source_by_stream.pop(stream_id, None)
        if next_output is not None:
            self._queued_sessions.discard(next_output.intent.session_id)
            self._play_queued_output(next_output)
        for listener in list(self._finish_listeners):
            listener(user_id, session_id or "", stream_id)

    def _play_queued_output(self, queued: QueuedOutput) -> None:
        stream_id = self.arbiter.activate_queued(queued, format=queued.source.stream_format())
        self._stream_by_session[queued.intent.session_id] = stream_id
        self._source_by_stream[stream_id] = queued.source
        payload = queued.source.synthesize_pending()
        if payload:
            chunk = StreamChunk(
                user_id=queued.intent.user_id,
                session_id=queued.intent.session_id,
                stream_id=stream_id,
                stream_type="actuator.speaker",
                seq=self._seq_by_stream.get(stream_id, 0),
                payload=payload,
                sample_rate=self.stream_service.registry.get(stream_id).format.sample_rate,
                duration_ms=40,
                final=False,
            )
            self.stream_service.write_chunk(chunk)
            self._payload_by_stream.setdefault(stream_id, bytearray()).extend(payload)
            self.recorder.record_agent_event(
                queued.intent.session_id,
                {
                    "event": "assistant_audio.delta",
                    "stream_id": stream_id,
                    "payload_size": len(payload),
                    "tts": queued.source.metrics(),
                    "stream_format": self.stream_service.registry.get(stream_id).format.__dict__,
                },
            )
            self._seq_by_stream[stream_id] = 1
        if queued.source.final_requested:
            self._finish_stream(queued.intent.user_id, queued.intent.session_id, stream_id, queued.source)

    def _intent_with_defaults(self, intent: OutputItem) -> OutputItem:
        return OutputItem(
            user_id=intent.user_id,
            session_id=intent.session_id,
            source=intent.source,
            priority=intent.priority or self.default_priority,
            on_interrupted=intent.on_interrupted if intent.on_interrupted is not None else self.default_on_interrupted,
            on_blocked=intent.on_blocked if intent.on_blocked is not None else self.default_on_blocked,
            ttl_seconds=intent.ttl_seconds,
            dedupe_key=intent.dedupe_key,
            cached_prompt_key=intent.cached_prompt_key,
        )

    def _split_native_audio_payload(self, payload: bytes, *, source: NativeAudioOutputSource) -> list[bytes]:
        """把 provider 原生音频拆成协议 chunk。

        主要逻辑：Omni 可能一次返回数百毫秒音频，而 `StreamChunk` 的 header 需要声明
        真实 chunk_ms，且每片不能超过 `stream.max_chunk_bytes`。这里按输出格式的
        chunk_ms 计算目标大小，再受 max_chunk_bytes 约束拆分。
        参数：`payload` 为 provider 原始 PCM；`source` 提供输出音频格式。
        返回值：拆分后的 payload 列表。
        异常情况：无。
        """
        target_size = min(source.chunk_bytes(), self.stream_service.max_chunk_bytes)
        if source.format.codec == "pcm16le":
            frame_bytes = max(2, source.format.channels * 2)
            target_size = max(frame_bytes, target_size - (target_size % frame_bytes))
        if target_size <= 0:
            target_size = len(payload)
        return [payload[offset : offset + target_size] for offset in range(0, len(payload), target_size)]

    def _new_tts(self) -> StreamingTTS:
        if self._injected_tts is not None:
            return self._injected_tts
        tts, downgrade_reason = build_tts_provider(self.tts_config)
        if downgrade_reason:
            self.recorder.record_system_event(
                {"event": "system.degradation.raised", "component": "StreamingTTS", "reason": downgrade_reason}
            )
        if not self.tts_config.streaming or not getattr(tts, "streaming", True):
            self.recorder.record_system_event(
                {
                    "event": "system.degradation.raised",
                    "component": "StreamingTTS",
                    "reason": "tts_provider_streaming_disabled",
                    "provider": getattr(tts, "provider_name", "unknown"),
                    "model": getattr(tts, "model", "unknown"),
                }
            )
        return tts

    def submit_cached_prompt_audio(
        self,
        *,
        intent: OutputItem,
        cache_key: str,
        text: str,
        format: StreamFormat,
    ) -> PlaybackDecision:
        """提交缓存提示音。"""

        key = (cache_key, format.sample_rate, format.channels)
        audio = self._cached_audio_by_key.get(key)
        cached_hit = audio is not None
        if audio is None:
            audio = MockStreamingTTS(sample_rate_hz=format.sample_rate).synthesize_delta(text)
            self._cached_audio_by_key[key] = audio
        source = CachedAudioOutputSource(cache_key=cache_key, audio=audio, format=format, metadata={"cached": cached_hit})
        resolved_intent = self._intent_with_defaults(
            OutputItem(
                user_id=intent.user_id,
                session_id=intent.session_id,
                source=intent.source or "cached_prompt_audio",
                priority=intent.priority,
                on_interrupted=intent.on_interrupted,
                on_blocked=intent.on_blocked,
                ttl_seconds=intent.ttl_seconds,
                dedupe_key=intent.dedupe_key,
                cached_prompt_key=cache_key,
            )
        )
        decision, stream_id = self.arbiter.submit(source=source, intent=resolved_intent, format=format)
        if decision.action in {"drop", "queue"} or stream_id is None:
            return decision
        self._stream_by_session[resolved_intent.session_id] = stream_id
        self._source_by_stream[stream_id] = source
        payload = source.synthesize_pending()
        if payload:
            chunk = StreamChunk(
                user_id=resolved_intent.user_id,
                session_id=resolved_intent.session_id,
                stream_id=stream_id,
                stream_type="actuator.speaker",
                seq=self._seq_by_stream.get(stream_id, 0),
                payload=payload,
                codec=format.codec,
                sample_rate=format.sample_rate,
                channels=format.channels,
                duration_ms=format.chunk_ms,
                final=False,
                metadata={"source": "cached_prompt_audio", "cache_key": cache_key},
            )
            self.stream_service.write_chunk(chunk)
            self._payload_by_stream.setdefault(stream_id, bytearray()).extend(payload)
            self.recorder.record_agent_event(
                resolved_intent.session_id,
                {
                    "event": "assistant_audio.delta",
                    "stream_id": stream_id,
                    "payload_size": len(payload),
                    "cached_prompt_audio": source.metrics(),
                    "stream_format": format.__dict__,
                },
            )
        self._finish_stream(resolved_intent.user_id, resolved_intent.session_id, stream_id, source)
        return decision

    def submit_tool_progress_audio(
        self,
        *,
        user_id: str,
        session_id: str,
        tool_name: str,
        messages: list[str],
        generation_mode: str | None = None,
    ) -> PlaybackDecision | None:
        """提交工具前置播报音频。

        主要逻辑：`cached` 模式复用提示音缓存；`realtime` 模式走当前 TTS 流式输出。
        参数：`messages` 为 Tool 声明的候选文案，当前选择第一条可用文案。
        返回值：播放仲裁决策；没有文案时返回 None。
        异常情况：下游 stream 写入失败时继续抛出，便于验收暴露链路问题。
        """

        text = next((str(item).strip() for item in messages if str(item).strip()), "")
        if not text:
            return None
        mode = (generation_mode or self.tool_progress_audio_mode or "cached").strip().lower()
        cache_key = f"tool-progress:{tool_name}:{text}"
        intent = OutputItem(
            user_id=user_id,
            session_id=session_id,
            source="tool_progress_audio",
            priority=self.tool_progress_priority,
            ttl_seconds=self.tool_progress_ttl_seconds,
            dedupe_key=cache_key,
            cached_prompt_key=cache_key,
        )
        if mode == "realtime":
            self.on_agent_text_delta(
                AssistantTextDelta(user_id=user_id, session_id=session_id, text=text, final=False, intent=intent)
            )
            self.on_agent_text_delta(
                AssistantTextDelta(user_id=user_id, session_id=session_id, text="", final=True, intent=intent)
            )
            active_stream_id = self._stream_by_session.get(session_id)
            decision = PlaybackDecision(
                action="submitted",
                reason="tool_progress_realtime_tts",
                active_stream_id=active_stream_id,
                user_id=user_id,
                session_id=session_id,
                priority=self.tool_progress_priority,
            )
            self.recorder.record_agent_event(
                session_id,
                {
                    "event": "tool.progress_audio.submitted",
                    "source": "tool_progress_audio",
                    "tool_name": tool_name,
                    "generation_mode": mode,
                    "message": text,
                    "decision": decision.__dict__,
                },
            )
            return decision
        decision = self.submit_cached_prompt_audio(
            intent=intent,
            cache_key=cache_key,
            text=text,
            format=StreamFormat(codec="pcm16le", sample_rate=self.tts_config.sample_rate_hz, channels=1, chunk_ms=40),
        )
        self.recorder.record_agent_event(
            session_id,
            {
                "event": "tool.progress_audio.submitted",
                "source": "tool_progress_audio",
                "tool_name": tool_name,
                "generation_mode": mode,
                "message": text,
                "decision": decision.__dict__,
            },
        )
        return decision


class OutputService:
    def __init__(
        self,
        *,
        stream_service: StreamService,
        recorder: RunRecorder,
        tts_config: TtsProviderConfig | None = None,
        default_priority: str = "normal",
        default_on_blocked: str = "queue",
        default_on_interrupted: str = "drop",
        max_queue_size: int = 32,
        tool_progress_audio_mode: str = "cached",
        tool_progress_priority: str = "low",
        tool_progress_ttl_seconds: int = 10,
    ) -> None:
        self.router = OutputRouter(
            stream_service=stream_service,
            recorder=recorder,
            tts_config=tts_config,
            default_priority=default_priority,
            default_on_blocked=default_on_blocked,
            default_on_interrupted=default_on_interrupted,
            max_queue_size=max_queue_size,
            tool_progress_audio_mode=tool_progress_audio_mode,
            tool_progress_priority=tool_progress_priority,
            tool_progress_ttl_seconds=tool_progress_ttl_seconds,
        )
        self.notification_coordinator = NotificationCoordinator(output_service=self)

    def on_assistant_text_delta(self, delta: AssistantTextDelta) -> None:
        self.router.on_agent_text_delta(delta)

    def add_output_finished_listener(self, listener: Callable[[str, str, str], None]) -> None:
        """注册当前输出完成回调。"""

        self.router.add_finish_listener(listener)

    def mark_endpoint_playback_finished(self, *, user_id: str, session_id: str | None, stream_id: str | None) -> None:
        """接收端侧播放完成回报。

        主要逻辑：把端侧 `stream.output.finished/closed` 转为 Output Router 的播放完成信号。
        参数：`user_id` 为用户标识；`session_id` 为端侧会话；`stream_id` 为完成的输出流。
        返回值：无。
        异常情况：无。
        """

        self.router.mark_endpoint_playback_finished(user_id, session_id, stream_id)

    def on_assistant_audio_delta(
        self,
        *,
        user_id: str,
        session_id: str,
        audio: bytes,
        format: StreamFormat,
        final: bool = False,
        intent: OutputItem | None = None,
        metadata: dict | None = None,
    ) -> None:
        """接收原生 assistant_audio.delta。

        主要逻辑：Omni Realtime 直接返回 audio delta 时调用本入口，不走 TTS。
        参数：`audio` 为 PCM payload，`format` 为输出流格式，`final` 表示 provider
        audio done。
        返回值：无。
        异常情况：stream 写入失败时抛出异常。
        """
        self.router.on_assistant_audio_delta(
            user_id=user_id,
            session_id=session_id,
            audio=audio,
            format=format,
            final=final,
            intent=intent,
            metadata=metadata,
        )

    def submit_text(self, *, user_id: str, session_id: str, text: str, priority: str = "normal", ttl_seconds: int = 0) -> None:
        """提交文本到 Output Service。

        主要逻辑：由服务内部创建 `OutputItem`，避免 Tool / Task 直接接触输出调度对象。
        参数：`user_id`、`session_id` 定位会话，`text` 为文本，`priority` 和
        `ttl_seconds` 为内部调度提示。
        返回值：无。
        异常情况：下游 stream 写入失败时向上抛出。
        """
        self.submit_output(OutputItem(user_id=user_id, session_id=session_id, priority=priority, ttl_seconds=ttl_seconds), text)

    def submit_audio(
        self,
        *,
        user_id: str,
        session_id: str,
        audio: bytes,
        format: StreamFormat,
        priority: str = "normal",
        metadata: dict | None = None,
    ) -> None:
        """提交原生音频到 Output Service。

        主要逻辑：由服务内部创建 `OutputItem`，再走 native audio 输出链路。
        参数：`audio` 为音频 payload，`format` 为音频格式，`priority` 为调度优先级。
        返回值：无。
        异常情况：下游 stream 写入失败时向上抛出。
        """
        self.on_assistant_audio_delta(
            user_id=user_id,
            session_id=session_id,
            audio=audio,
            format=format,
            final=True,
            intent=OutputItem(user_id=user_id, session_id=session_id, source="tool_audio", priority=priority),
            metadata={"source": "tool_audio", **dict(metadata or {})},
        )

    def submit_cached_prompt_audio(
        self,
        *,
        user_id: str,
        session_id: str,
        cache_key: str,
        text: str,
        priority: str = "low",
        ttl_seconds: int = 10,
        format: StreamFormat | None = None,
    ) -> PlaybackDecision:
        """提交缓存提示音。"""

        return self.router.submit_cached_prompt_audio(
            intent=OutputItem(
                user_id=user_id,
                session_id=session_id,
                source="cached_prompt_audio",
                priority=priority,
                ttl_seconds=ttl_seconds,
                cached_prompt_key=cache_key,
            ),
            cache_key=cache_key,
            text=text,
            format=format or StreamFormat(codec="pcm16le", sample_rate=16000, channels=1, chunk_ms=40),
        )

    def submit_tool_progress(
        self,
        *,
        user_id: str,
        session_id: str,
        tool_name: str,
        messages: list[str],
        generation_mode: str | None = None,
    ) -> PlaybackDecision | None:
        """提交 Tool 前置播报。

        主要逻辑：供 Agent Core 在确认模型首输出是 tool call 后调用，避免普通文本回复
        被插入提示音。
        参数：`messages` 为候选提示文案；`generation_mode` 支持 cached/realtime。
        返回值：播放决策或 None。
        异常情况：无文案时返回 None。
        """

        return self.router.submit_tool_progress_audio(
            user_id=user_id,
            session_id=session_id,
            tool_name=tool_name,
            messages=messages,
            generation_mode=generation_mode,
        )

    def notify_task_event(self, event: Any) -> NotificationDecision:
        """接收任务事件通知。

        主要逻辑：从 TaskEvent 中提取文本、优先级和 TTL，交给 NotificationCoordinator。
        参数：`event` 为 TaskEvent 或同形对象。
        返回值：`NotificationDecision`。
        异常情况：下游输出失败时向上抛出。
        """
        payload = getattr(event, "payload", {}) or {}
        text = str(payload.get("text") or payload.get("message") or "")
        return self.notification_coordinator.submit(
            NotificationRequest(
                user_id=getattr(event, "user_id"),
                session_id=getattr(event, "session_id") or getattr(event, "task_id"),
                text=text,
                priority=getattr(event, "priority", "normal"),
                ttl_seconds=getattr(event, "ttl_seconds", 0),
                dedupe_key=getattr(event, "dedupe_key", None),
                merge_key=payload.get("merge_key") or payload.get("notification_group"),
                allow_direct_notify=bool(getattr(event, "allow_direct_notify", True)),
                requires_agent_context_sync=bool(getattr(event, "requires_agent_decision", False)),
                metadata={
                    "task_id": getattr(event, "task_id", ""),
                    "task_type": getattr(event, "task_type", ""),
                    "event_name": getattr(event, "event_name", ""),
                },
            )
        )

    def submit_output(self, intent: OutputItem, text: str) -> None:
        """提交完整文本输出。

        主要逻辑：Tool / Task 通知已经是完整文本，优先使用 TTS provider 的一次性合成能力，
        再作为原生音频交给 Output Router。这样不会先打开空的 speaker stream，也不会因为
        DashScope 流式任务尚未启动而在 `streaming_complete()` 阶段失败。
        参数：`intent` 为内部输出意图，`text` 为完整播报文本。
        返回值：无。
        异常情况：TTS 合成或 stream 写入失败时向上抛出，由 TaskEventBridge 记录系统错误。
        """

        if not text:
            return
        tts = self.router._new_tts()
        synthesize_text = getattr(tts, "synthesize_text", None)
        if callable(synthesize_text):
            audio = synthesize_text(text)
            if not audio:
                raise RuntimeError("TTS provider returned empty audio for complete text output")
            metrics = tts.metrics()
            self.router.on_assistant_audio_delta(
                user_id=intent.user_id,
                session_id=intent.session_id,
                audio=audio,
                format=StreamFormat(
                    codec="pcm16le",
                    sample_rate=int(metrics.get("sample_rate_hz") or self.router.tts_config.sample_rate_hz),
                    channels=1,
                    chunk_ms=40,
                ),
                final=True,
                intent=OutputItem(
                    user_id=intent.user_id,
                    session_id=intent.session_id,
                    source=intent.source or "text_tts",
                    priority=intent.priority,
                    on_interrupted=intent.on_interrupted,
                    on_blocked=intent.on_blocked,
                    ttl_seconds=intent.ttl_seconds,
                    dedupe_key=intent.dedupe_key,
                    cached_prompt_key=intent.cached_prompt_key,
                ),
                metadata={"source": "text_tts", "tts": metrics},
            )
            return
        self.router.on_agent_text_delta(
            AssistantTextDelta(user_id=intent.user_id, session_id=intent.session_id, text=text, final=False, intent=intent)
        )
        self.router.on_agent_text_delta(
            AssistantTextDelta(user_id=intent.user_id, session_id=intent.session_id, text="", final=True, intent=intent)
        )

    def interrupt_user(self, user_id: str, *, session_id: str | None, reason: str) -> PlaybackDecision:
        decision = self.router.arbiter.cancel_current(user_id, session_id=session_id, reason=reason)
        if decision.interrupted_stream_id:
            self.router._source_by_stream.pop(decision.interrupted_stream_id, None)
            for stored_session, stream_id in list(self.router._stream_by_session.items()):
                if stream_id == decision.interrupted_stream_id:
                    self.router._stream_by_session.pop(stored_session, None)
                    self.router._native_source_by_session.pop(stored_session, None)
        return decision

    def active_output_stream_id(self, user_id: str, session_id: str | None = None) -> str | None:
        """查询用户当前活跃 output stream。

        主要逻辑：只暴露只读状态，供音频会话生命周期判断是否可以执行
        `close_after_reply`。
        参数：`user_id` 为用户标识，`session_id` 可选用于进一步限定当前会话。
        返回值：活跃 stream_id；没有活跃输出时返回 None。
        异常情况：无。
        """

        active = self.router.arbiter._active_by_user.get(user_id)
        if active is None:
            return None
        intent, stream_id, _source = active
        if session_id is not None and intent.session_id != session_id:
            return None
        return stream_id

    def debug_snapshot(self) -> dict:
        """返回 Output Service 调试快照。"""

        snapshot = self.router.arbiter.debug_snapshot()
        snapshot["notifications"] = {"recent_decisions": self.notification_coordinator.recent_decisions()}
        return snapshot
