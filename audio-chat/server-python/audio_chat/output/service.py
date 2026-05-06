from __future__ import annotations

import os
import queue
import time
import audioop
from dataclasses import dataclass, field
from typing import Protocol

from audio_chat.observability import RunRecorder
from audio_chat.protocol import SERVER_PRODUCER_ID, StreamChunk, StreamFormat, new_id
from audio_chat.stream import StreamService

PRIORITY_ORDER = {"low": 0, "normal": 1, "high": 2, "critical": 3}


@dataclass(frozen=True)
class OutputIntent:
    user_id: str
    session_id: str
    source: str = "agent_reply"
    priority: str = "normal"
    on_interrupted: str = "drop"
    on_blocked: str = "drop"
    ttl_seconds: int = 0
    dedupe_key: str | None = None


@dataclass(frozen=True)
class AssistantTextDelta:
    user_id: str
    session_id: str
    text: str
    final: bool = False
    intent: OutputIntent | None = None


@dataclass(frozen=True)
class PlaybackDecision:
    action: str
    reason: str
    active_stream_id: str | None = None
    interrupted_stream_id: str | None = None
    queued_intent_id: str | None = None


@dataclass(frozen=True)
class TtsProviderConfig:
    provider: str = "mock"
    model: str = "mock-tts"
    voice: str = "mock"
    streaming: bool = True
    allow_mock_fallback: bool = True
    websocket_api_url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
    sample_rate_hz: int = 22050


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
    provider_name = "mock"
    streaming = True

    def __init__(self, model: str = "mock-tts", voice: str = "mock", sample_rate_hz: int = 16000) -> None:
        self.model = model
        self.voice = voice
        self.sample_rate_hz = sample_rate_hz

    def synthesize_delta(self, text: str) -> bytes:
        if not text:
            return b""
        samples = max(320, len(text.encode("utf-8")) * 40)
        return (b"\x01\x00" * samples)[: samples * 2]

    def metrics(self) -> dict:
        return {
            "provider": self.provider_name,
            "model": self.model,
            "voice": self.voice,
            "sample_rate_hz": self.sample_rate_hz,
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
        self._synthesizer = SpeechSynthesizer(model=model, voice=voice, format=fmt, callback=_Callback())

    def synthesize_delta(self, text: str) -> bytes:
        if not text:
            return b""
        if self._first_text_at is None:
            self._first_text_at = time.time()
        self._text_push_count += 1
        self._text_chars += len(text)
        self._synthesizer.streaming_call(text)
        deadline = time.time() + 3.0
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
    """原生音频输出源占位。

    主要功能：为后续模型直接输出 audio delta 预留边界；当前阶段不主动使用。
    主要属性：`format` 描述原生音频格式，`chunks` 保存待播放音频。
    """

    format: StreamFormat
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
        return {"provider": "native_audio", "sample_rate_hz": self.format.sample_rate}

    def finish(self) -> None:
        return None


@dataclass
class QueuedOutput:
    source: StreamingTtsOutputSource | NativeAudioOutputSource
    intent: OutputIntent
    created_at: float = field(default_factory=time.time)
    source_stream_id: str | None = None


class PlaybackArbiter:
    def __init__(self, *, stream_service: StreamService, recorder: RunRecorder, max_queue_size: int = 32) -> None:
        self.stream_service = stream_service
        self.recorder = recorder
        self.max_queue_size = max_queue_size
        self._active_by_user: dict[str, tuple[OutputIntent, str, StreamingTtsOutputSource | NativeAudioOutputSource]] = {}
        self._queue_by_user: dict[str, list[QueuedOutput]] = {}

    def submit(
        self,
        *,
        source: StreamingTtsOutputSource | NativeAudioOutputSource,
        intent: OutputIntent,
        format: StreamFormat | None = None,
    ) -> tuple[PlaybackDecision, str | None]:
        active = self._active_by_user.get(intent.user_id)
        if active is None:
            stream_id = self._open_output_stream(intent, source=source, format=format)
            decision = PlaybackDecision(action="play_now", reason="no_active_playback", active_stream_id=stream_id)
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
            )
            self._record(intent.session_id, decision)
            return decision, stream_id
        if intent.on_blocked == "queue":
            queue = self._queue_by_user.setdefault(intent.user_id, [])
            if len(queue) >= self.max_queue_size:
                decision = PlaybackDecision(action="drop", reason="queue_full")
                self._record(intent.session_id, decision)
                return decision, None
            queue.append(QueuedOutput(source=source, intent=intent))
            decision = PlaybackDecision(action="queue", reason="active_playback_not_preempted")
            self._record(intent.session_id, decision)
            return decision, None
        decision = PlaybackDecision(action="drop", reason="active_playback_not_preempted")
        self._record(intent.session_id, decision)
        return decision, None

    def on_playback_finished(self, user_id: str, stream_id: str) -> QueuedOutput | None:
        active = self._active_by_user.get(user_id)
        if active and active[1] == stream_id:
            self._active_by_user.pop(user_id, None)
        return self.pop_next(user_id)

    def cancel_current(self, user_id: str, *, session_id: str | None, reason: str) -> PlaybackDecision:
        active = self._active_by_user.pop(user_id, None)
        if active is None:
            decision = PlaybackDecision(action="cancel_current", reason="no_active_playback")
            self._record(session_id or "interruptions", decision)
            return decision
        _intent, stream_id, _source = active
        self.stream_service.cancel_stream(stream_id, reason=reason)
        decision = PlaybackDecision(action="cancel_current", reason=reason, interrupted_stream_id=stream_id)
        self._record(session_id or "interruptions", decision)
        return decision

    def pop_next(self, user_id: str) -> QueuedOutput | None:
        queue = self._queue_by_user.get(user_id, [])
        while queue:
            queued = queue.pop(0)
            if queued.intent.ttl_seconds and time.time() - queued.created_at > queued.intent.ttl_seconds:
                self._record(queued.intent.session_id, PlaybackDecision(action="drop", reason="ttl_expired"))
                continue
            return queued
        return None

    def activate_queued(self, queued: QueuedOutput, *, format: StreamFormat | None = None) -> str:
        stream_id = self._open_output_stream(queued.intent, source=queued.source, format=format)
        self._record(
            queued.intent.session_id,
            PlaybackDecision(action="play_now", reason="queued_playback_ready", active_stream_id=stream_id),
        )
        return stream_id

    def _open_output_stream(
        self,
        intent: OutputIntent,
        *,
        source: StreamingTtsOutputSource | NativeAudioOutputSource,
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
        self.recorder.record_playback_decision(session_id, decision.__dict__)


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
    ) -> None:
        self.stream_service = stream_service
        self.recorder = recorder
        self.tts_config = tts_config or TtsProviderConfig()
        self._injected_tts = tts
        self.default_priority = default_priority
        self.default_on_blocked = default_on_blocked
        self.default_on_interrupted = default_on_interrupted
        self.arbiter = PlaybackArbiter(stream_service=stream_service, recorder=recorder, max_queue_size=max_queue_size)
        self._stream_by_session: dict[str, str] = {}
        self._seq_by_stream: dict[str, int] = {}
        self._source_by_session: dict[str, StreamingTtsOutputSource] = {}
        self._source_by_stream: dict[str, StreamingTtsOutputSource | NativeAudioOutputSource] = {}
        self._queued_sessions: set[str] = set()

    def on_agent_text_delta(self, delta: AssistantTextDelta) -> None:
        intent = self._intent_with_defaults(delta.intent or OutputIntent(user_id=delta.user_id, session_id=delta.session_id))
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

    def _finish_stream(
        self,
        user_id: str,
        session_id: str,
        stream_id: str,
        source: StreamingTtsOutputSource | NativeAudioOutputSource,
    ) -> None:
        source.finish()
        self.stream_service.close_stream(stream_id, reason="assistant_audio.done")
        self._stream_by_session.pop(session_id, None)
        self._source_by_stream.pop(stream_id, None)
        next_output = self.arbiter.on_playback_finished(user_id, stream_id)
        if next_output is not None:
            self._queued_sessions.discard(next_output.intent.session_id)
            self._play_queued_output(next_output)

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

    def _intent_with_defaults(self, intent: OutputIntent) -> OutputIntent:
        return OutputIntent(
            user_id=intent.user_id,
            session_id=intent.session_id,
            source=intent.source,
            priority=intent.priority or self.default_priority,
            on_interrupted=intent.on_interrupted if intent.on_interrupted != "drop" else self.default_on_interrupted,
            on_blocked=intent.on_blocked if intent.on_blocked != "drop" else self.default_on_blocked,
            ttl_seconds=intent.ttl_seconds,
            dedupe_key=intent.dedupe_key,
        )

    def _new_tts(self) -> StreamingTTS:
        if self._injected_tts is not None:
            return self._injected_tts
        tts, downgrade_reason = build_tts_provider(self.tts_config)
        if downgrade_reason:
            self.recorder.record_system_event(
                {"event": "system.degradation.raised", "component": "StreamingTTS", "reason": downgrade_reason}
            )
        return tts


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
    ) -> None:
        self.router = OutputRouter(
            stream_service=stream_service,
            recorder=recorder,
            tts_config=tts_config,
            default_priority=default_priority,
            default_on_blocked=default_on_blocked,
            default_on_interrupted=default_on_interrupted,
            max_queue_size=max_queue_size,
        )

    def on_assistant_text_delta(self, delta: AssistantTextDelta) -> None:
        self.router.on_agent_text_delta(delta)

    def submit_output(self, intent: OutputIntent, text: str) -> None:
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
        return decision
