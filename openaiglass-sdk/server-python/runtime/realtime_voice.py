"""全双工实时语音运行时。"""

from __future__ import annotations

import base64
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from infra.errors import ErrorCode, build_error
from infra.logging import LogContext, get_logger, log_debug
from protocol.media import MediaFrame
from runtime.playback_arbiter import PlaybackArbiter, PlaybackIntent


REALTIME_INPUT_FRAME_TYPES = {"voice.realtime.input.delta", "realtime_audio_chunk"}
DEFAULT_REALTIME_SAMPLE_RATE_HZ = 16000
DEFAULT_REALTIME_CHANNELS = 1


@dataclass(slots=True)
class RealtimeInputStream:
    """实时语音上行输入流。

    主要功能：
    1. 记录用户一次连续说话的输入流编号和帧序号。
    2. 保存少量音频统计信息，供快照和回放断言使用。
    """

    input_stream_id: str
    started_at_ms: int
    reason: str = "vad_speech"
    first_audio_at_ms: int | None = None
    committed_at_ms: int | None = None
    frame_count: int = 0
    bytes_received: int = 0
    last_chunk_index: int | None = None
    final_transcript: str | None = None
    finish_reason: str | None = None


@dataclass(slots=True)
class RealtimeOutputStream:
    """实时语音下行输出流。

    主要功能：
    1. 表示模型或 SDK 正在下发给设备的一条实时输出。
    2. 记录播放仲裁意图和输出状态，避免迟到分片复活。
    """

    output_stream_id: str
    intent_id: str
    source: str
    priority: str
    interrupt_policy: str
    started_at_ms: int
    first_delta_at_ms: int | None = None
    chunk_count: int = 0
    bytes_sent: int = 0
    state: str = "streaming"
    cancel_reason: str | None = None


@dataclass(slots=True)
class RealtimeVoiceSession:
    """单设备全双工实时语音会话。

    主要功能：
    1. 保存会话模式、状态、输入输出流和最近事件。
    2. 维护端侧能力与运行态指标，便于真机联调定位问题。
    """

    device_id: str
    device_type: str
    session_id: str
    requested_mode: str = "full_duplex_realtime"
    accepted_mode: str = "full_duplex_realtime"
    state: str = "opening"
    sample_rate: int = DEFAULT_REALTIME_SAMPLE_RATE_HZ
    channels: int = DEFAULT_REALTIME_CHANNELS
    codec: str = "pcm16"
    min_barge_in_confidence: float = 0.72
    clear_agent_output_on_interrupt: bool = True
    capabilities: dict[str, Any] = field(default_factory=dict)
    active_input: RealtimeInputStream | None = None
    active_output: RealtimeOutputStream | None = None
    cancelled_output_stream_ids: set[str] = field(default_factory=set)
    recent_events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=64))
    recent_interrupts: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=32))
    latency_metrics: dict[str, int | None] = field(
        default_factory=lambda: {
            "input_first_audio_ms": None,
            "vad_trigger_ms": None,
            "interrupt_decision_ms": None,
            "model_first_delta_ms": None,
            "output_first_audio_ms": None,
        }
    )
    counters: dict[str, int] = field(default_factory=lambda: {"barge_in_count": 0, "echo_rejected_count": 0})
    opened_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))


@dataclass(slots=True)
class RealtimeModelResponse:
    """实时模型适配器输出。

    主要功能：
    1. 用统一结构表达最终转写、文本增量和音频增量。
    2. 让单测和回放不依赖真实模型供应商。
    """

    final_transcript: str = ""
    output_stream_id: str = ""
    text_delta: str = ""
    audio_pcm_bytes: bytes = b""
    finish_reason: str = "completed"


class RealtimeModelAdapter(Protocol):
    """实时模型适配器接口。

    主要功能：
    1. 屏蔽真实实时模型供应商协议差异。
    2. 把 SDK 内部输入流提交转换成统一输出事件。
    """

    def commit_input(self, *, session: RealtimeVoiceSession, input_stream: RealtimeInputStream) -> list[RealtimeModelResponse]:
        """提交一次用户输入流并返回统一模型响应。"""


class LoopbackRealtimeModelAdapter:
    """回放和单测使用的实时模型适配器。

    主要功能：
    1. 不访问外部服务。
    2. 按输入流编号生成确定性文本和少量静音音频。
    """

    def commit_input(self, *, session: RealtimeVoiceSession, input_stream: RealtimeInputStream) -> list[RealtimeModelResponse]:
        """生成确定性 loopback 回复。"""

        output_stream_id = f"rt_out_{input_stream.input_stream_id}"
        transcript = input_stream.final_transcript or f"realtime input {input_stream.input_stream_id}"
        return [
            RealtimeModelResponse(
                final_transcript=transcript,
                output_stream_id=output_stream_id,
                text_delta=f"收到：{transcript}",
                audio_pcm_bytes=b"\x00\x00" * 160,
            )
        ]


class HalfDuplexFallbackRealtimeModelAdapter:
    """全双工不可用时的半双工降级适配器。

    主要功能：
    1. 为 SDK 保留统一 Adapter 入口。
    2. 明确告诉调用方当前没有真实实时模型输出，应该回到半双工 ASR/TTS 路径。
    """

    def commit_input(self, *, session: RealtimeVoiceSession, input_stream: RealtimeInputStream) -> list[RealtimeModelResponse]:
        """返回空响应，表示需要走半双工降级链路。"""

        return [
            RealtimeModelResponse(
                final_transcript=input_stream.final_transcript or "",
                finish_reason="degraded_to_half_duplex",
            )
        ]


class RealtimeVoiceRuntime:
    """全双工实时语音运行时。

    主要功能：
    1. 管理实时语音会话状态机。
    2. 接收上行媒体帧、用户打断和模型输出事件。
    3. 复用 `PlaybackArbiter` 统一处理实时输出和用户插话。
    """

    def __init__(
        self,
        *,
        playback_arbiter: PlaybackArbiter,
        send_control_message: Callable[[str, str, str, str, dict[str, Any]], None],
        model_adapter: RealtimeModelAdapter | None = None,
        max_recent_events: int = 64,
    ) -> None:
        self._playback_arbiter = playback_arbiter
        self._send_control_message = send_control_message
        self._model_adapter = model_adapter or LoopbackRealtimeModelAdapter()
        self._max_recent_events = max_recent_events
        self._sessions: dict[str, RealtimeVoiceSession] = {}
        self._logger = get_logger("server.realtime_voice")

    def build_open_payload(self) -> dict[str, Any]:
        """生成服务端下发给端侧的全双工打开请求。"""

        return {
            "mode": "full_duplex_realtime",
            "input": {
                "sample_rate": DEFAULT_REALTIME_SAMPLE_RATE_HZ,
                "channels": DEFAULT_REALTIME_CHANNELS,
                "codec": "pcm16",
                "frame_ms": 20,
                "aec_required": True,
                "vad_required": True,
            },
            "output": {
                "sample_rate": DEFAULT_REALTIME_SAMPLE_RATE_HZ,
                "channels": DEFAULT_REALTIME_CHANNELS,
                "codec": "pcm16",
                "frame_ms": 20,
            },
            "interrupt": {
                "enabled": True,
                "min_barge_in_confidence": 0.72,
                "clear_agent_output_on_interrupt": True,
            },
            "transport": "websocket_media_frame",
        }

    def open_session(
        self,
        *,
        device_id: str,
        device_type: str,
        session_id: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """创建或重置一条实时语音会话。"""

        payload = payload or self.build_open_payload()
        input_config = payload.get("input") if isinstance(payload.get("input"), dict) else {}
        interrupt_config = payload.get("interrupt") if isinstance(payload.get("interrupt"), dict) else {}
        session = RealtimeVoiceSession(
            device_id=device_id,
            device_type=device_type,
            session_id=session_id,
            requested_mode=str(payload.get("mode") or "full_duplex_realtime"),
            sample_rate=int(input_config.get("sample_rate") or DEFAULT_REALTIME_SAMPLE_RATE_HZ),
            channels=int(input_config.get("channels") or DEFAULT_REALTIME_CHANNELS),
            codec=str(input_config.get("codec") or "pcm16"),
            min_barge_in_confidence=float(interrupt_config.get("min_barge_in_confidence") or 0.72),
            clear_agent_output_on_interrupt=bool(interrupt_config.get("clear_agent_output_on_interrupt", True)),
        )
        self._sessions[device_id] = session
        self._record_event(session, "voice.realtime.session.open", {"requested_mode": session.requested_mode})
        return self._opened_payload(session)

    def on_session_opened(self, *, device_id: str, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """处理端侧确认实时会话已打开。"""

        session = self._require_session(device_id=device_id, session_id=session_id)
        capabilities = payload.get("capabilities") if isinstance(payload.get("capabilities"), dict) else {}
        accepted_mode = str(payload.get("accepted_mode") or session.requested_mode)
        if capabilities.get("aec") in {False, "none", "unsupported"}:
            accepted_mode = "half_duplex"
            session.state = "degraded"
            self._record_event(
                session,
                "voice.realtime.session.degraded",
                {"from": session.requested_mode, "to": accepted_mode, "reason": "endpoint_aec_not_available"},
            )
        else:
            session.state = "listening"
        session.accepted_mode = accepted_mode
        session.capabilities = dict(capabilities)
        self._record_event(session, "voice.realtime.session.opened", {"accepted_mode": accepted_mode})
        return self._opened_payload(session)

    def on_input_started(self, *, device_id: str, session_id: str, payload: dict[str, Any]) -> None:
        """处理端侧上报的用户开始说话事件。"""

        session = self._require_session(device_id=device_id, session_id=session_id)
        input_stream_id = str(payload.get("input_stream_id") or payload.get("stream_id") or "").strip()
        if not input_stream_id:
            raise build_error(ErrorCode.INVALID_MESSAGE, "voice.realtime.input.started 缺少 input_stream_id")
        now_ms = self._now_ms()
        session.active_input = RealtimeInputStream(
            input_stream_id=input_stream_id,
            started_at_ms=now_ms,
            reason=str(payload.get("reason") or "vad_speech"),
        )
        session.latency_metrics["vad_trigger_ms"] = self._latency_ms(session.opened_at_ms, now_ms)
        session.state = "user_speaking"
        self._record_event(session, "voice.realtime.input.started", {"input_stream_id": input_stream_id})

    def on_audio_frame(self, *, device_id: str, frame: MediaFrame) -> bool:
        """接收实时语音上行媒体帧。

        返回值：
        1. `True` 表示该帧已经被实时运行时处理。
        2. `False` 表示这不是实时语音帧，可交给半双工链路处理。
        """

        header = frame.header
        if str(header.get("frame_type") or "") not in REALTIME_INPUT_FRAME_TYPES:
            return False
        session_id = str(header.get("session_id") or "").strip()
        session = self._require_session(device_id=device_id, session_id=session_id)
        input_stream_id = str(header.get("input_stream_id") or header.get("stream_id") or "").strip()
        if not input_stream_id:
            raise build_error(ErrorCode.INVALID_MESSAGE, "实时音频帧缺少 input_stream_id")
        if session.active_input is None or session.active_input.input_stream_id != input_stream_id:
            session.active_input = RealtimeInputStream(
                input_stream_id=input_stream_id,
                started_at_ms=self._now_ms(),
                reason="implicit_audio_frame",
            )
            session.state = "user_speaking"
            self._record_event(session, "voice.realtime.input.started", {"input_stream_id": input_stream_id})

        active_input = session.active_input
        chunk_index = int(header.get("chunk_index", header.get("seq", 0)))
        if active_input.last_chunk_index is not None and chunk_index <= active_input.last_chunk_index:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "实时音频帧序号倒退或重复",
                details={"last_chunk_index": active_input.last_chunk_index, "chunk_index": chunk_index},
            )
        active_input.last_chunk_index = chunk_index
        active_input.frame_count += 1
        active_input.bytes_received += len(frame.payload)
        if active_input.first_audio_at_ms is None:
            now_ms = self._now_ms()
            active_input.first_audio_at_ms = now_ms
            session.latency_metrics["input_first_audio_ms"] = self._latency_ms(session.opened_at_ms, now_ms)

        self._maybe_record_echo_candidate(session=session, header=header)
        self._record_event(
            session,
            "voice.realtime.input.delta",
            {"input_stream_id": input_stream_id, "chunk_index": chunk_index, "payload_size": len(frame.payload)},
        )
        return True

    def on_input_committed(self, *, device_id: str, session_id: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """提交当前用户输入流并调用实时模型适配器。"""

        session = self._require_session(device_id=device_id, session_id=session_id)
        active_input = session.active_input
        if active_input is None:
            raise build_error(ErrorCode.STREAM_NOT_FOUND, "提交实时输入时没有活动输入流")
        expected_stream_id = str(payload.get("input_stream_id") or payload.get("stream_id") or "").strip()
        if expected_stream_id and expected_stream_id != active_input.input_stream_id:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "voice.realtime.input.committed.input_stream_id 与当前输入流不一致",
                details={"expected": active_input.input_stream_id, "actual": expected_stream_id},
            )
        active_input.committed_at_ms = self._now_ms()
        active_input.finish_reason = str(payload.get("finish_reason") or "endpoint_detected")
        active_input.final_transcript = str(payload.get("final_transcript") or payload.get("transcript") or "").strip() or None
        session.state = "model_streaming"
        self._record_event(
            session,
            "voice.realtime.input.committed",
            {"input_stream_id": active_input.input_stream_id, "finish_reason": active_input.finish_reason},
        )

        responses = self._model_adapter.commit_input(session=session, input_stream=active_input)
        emitted: list[dict[str, Any]] = []
        for response in responses:
            if response.final_transcript:
                active_input.final_transcript = response.final_transcript
            if response.output_stream_id and (response.text_delta or response.audio_pcm_bytes):
                emitted.append(
                    self.emit_output_delta(
                        device_id=device_id,
                        session_id=session_id,
                        output_stream_id=response.output_stream_id,
                        text_delta=response.text_delta,
                        audio_pcm_bytes=response.audio_pcm_bytes,
                    )
                )
            if response.finish_reason == "degraded_to_half_duplex":
                session.accepted_mode = "half_duplex"
                session.state = "degraded"
                self._record_event(
                    session,
                    "voice.realtime.session.degraded",
                    {"from": "full_duplex_realtime", "to": "half_duplex", "reason": "model_adapter_degraded"},
                )
        return emitted

    def emit_output_delta(
        self,
        *,
        device_id: str,
        session_id: str,
        output_stream_id: str,
        text_delta: str = "",
        audio_pcm_bytes: bytes = b"",
        source: str = "agent_reply",
        priority: str = "normal",
        interrupt_policy: str = "never",
    ) -> dict[str, Any]:
        """向设备下发一段实时输出。"""

        session = self._require_session(device_id=device_id, session_id=session_id)
        if output_stream_id in session.cancelled_output_stream_ids:
            self._record_event(
                session,
                "voice.realtime.output.delta.dropped",
                {"output_stream_id": output_stream_id, "reason": "late_output_after_interrupt"},
            )
            return {"dropped": True, "reason": "late_output_after_interrupt", "output_stream_id": output_stream_id}

        output = session.active_output
        if output is None or output.output_stream_id != output_stream_id:
            intent_id = f"{source}:{output_stream_id}"
            intent = PlaybackIntent(
                intent_id=intent_id,
                source=source,
                device_id=device_id,
                session_id=session_id,
                stream_id=output_stream_id,
                priority=priority,
                interrupt_policy=interrupt_policy,
            )
            submit_result = self._playback_arbiter.submit(intent)
            if submit_result.interrupted_intent is not None:
                self._send_control_message(
                    device_id,
                    "request",
                    "actuator.audio.interrupt",
                    session_id,
                    {
                        "device_id": device_id,
                        "stream_id": submit_result.interrupted_intent.stream_id,
                        "reason": "higher_priority_realtime_output",
                        "incoming_stream_id": output_stream_id,
                    },
                )
            if submit_result.decision.action == "queue":
                self._record_event(
                    session,
                    "voice.realtime.output.queued",
                    {"output_stream_id": output_stream_id, "reason": submit_result.decision.reason},
                )
                return {"queued": True, "decision": submit_result.decision.to_dict()}
            output = RealtimeOutputStream(
                output_stream_id=output_stream_id,
                intent_id=intent_id,
                source=source,
                priority=priority,
                interrupt_policy=interrupt_policy,
                started_at_ms=self._now_ms(),
            )
            session.active_output = output
            session.state = "playback_streaming"
            session.latency_metrics["model_first_delta_ms"] = self._latency_ms(
                session.active_input.committed_at_ms if session.active_input else None,
                output.started_at_ms,
            )

        now_ms = self._now_ms()
        if output.first_delta_at_ms is None:
            output.first_delta_at_ms = now_ms
            session.latency_metrics["output_first_audio_ms"] = self._latency_ms(output.started_at_ms, now_ms)
        output.chunk_count += 1
        output.bytes_sent += len(audio_pcm_bytes)
        payload = {
            "session_id": session_id,
            "output_stream_id": output_stream_id,
            "source": source,
            "chunk_index": output.chunk_count - 1,
            "format": "pcm16",
            "text_delta": text_delta,
            "audio_base64": base64.b64encode(audio_pcm_bytes).decode("ascii") if audio_pcm_bytes else "",
            "playback_intent_id": output.intent_id,
        }
        self._send_control_message(device_id, "notify", "voice.realtime.output.delta", session_id, payload)
        self._record_event(
            session,
            "voice.realtime.output.delta",
            {"output_stream_id": output_stream_id, "chunk_index": output.chunk_count - 1},
        )
        return {"sent": True, "output_stream_id": output_stream_id, "chunk_index": output.chunk_count - 1}

    def on_user_interrupt(self, *, device_id: str, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """处理全双工用户插话事件。"""

        session = self._require_session(device_id=device_id, session_id=session_id)
        reason = str(payload.get("reason") or "barge_in")
        clear_queue = bool(payload.get("clear_pending_playback", False))
        confidence = self._parse_float(payload.get("barge_in_confidence"), default=1.0)
        started_at_ms = self._now_ms()
        result = self._playback_arbiter.user_interrupt(
            device_id=device_id,
            session_id=session_id,
            reason=reason,
            clear_queue=clear_queue,
        )
        ended_at_ms = self._now_ms()
        session.latency_metrics["interrupt_decision_ms"] = self._latency_ms(started_at_ms, ended_at_ms)
        session.counters["barge_in_count"] += 1
        interrupted_stream_id = result.interrupted_intent.stream_id if result.interrupted_intent else None
        if interrupted_stream_id:
            session.cancelled_output_stream_ids.add(interrupted_stream_id)
        if session.active_output is not None and session.active_output.output_stream_id == interrupted_stream_id:
            session.active_output.state = "cancelled"
            session.active_output.cancel_reason = reason
            session.active_output = None
        interrupt_event = {
            "reason": reason,
            "barge_in_confidence": confidence,
            "interrupted_stream_id": interrupted_stream_id,
            "dropped_stream_ids": [intent.stream_id for intent in result.dropped_intents],
            "decision": result.decision.to_dict(),
            "created_at_ms": ended_at_ms,
        }
        session.recent_interrupts.append(interrupt_event)
        session.state = "user_speaking" if session.active_input is not None else "listening"
        self._record_event(session, "voice.realtime.user_interrupt", interrupt_event)
        if interrupted_stream_id:
            self._send_control_message(
                device_id,
                "request",
                "actuator.audio.interrupt",
                session_id,
                {
                    "device_id": device_id,
                    "stream_id": interrupted_stream_id,
                    "reason": reason,
                    "barge_in_confidence": confidence,
                    "clear_queue": clear_queue,
                },
            )
            self._send_control_message(
                device_id,
                "notify",
                "voice.realtime.output.cancelled",
                session_id,
                {
                    "session_id": session_id,
                    "output_stream_id": interrupted_stream_id,
                    "reason": reason,
                },
            )
        return interrupt_event

    def close_session(self, *, device_id: str, session_id: str, reason: str = "client_closed") -> None:
        """关闭实时语音会话。"""

        session = self._require_session(device_id=device_id, session_id=session_id)
        session.state = "closed"
        self._record_event(session, "voice.realtime.session.closed", {"reason": reason})

    def build_snapshot(self) -> dict[str, dict[str, Any]]:
        """导出全部实时语音会话快照。"""

        return {device_id: self._session_snapshot(session) for device_id, session in self._sessions.items()}

    def _maybe_record_echo_candidate(self, *, session: RealtimeVoiceSession, header: dict[str, Any]) -> None:
        """根据端侧字段记录回声候选，但不直接触发打断。"""

        voice_activity = str(header.get("voice_activity") or "").strip()
        confidence = self._parse_float(header.get("barge_in_confidence"), default=0.0)
        echo_suppressed = bool(header.get("echo_suppressed", False))
        if session.active_output is None:
            return
        if voice_activity == "echo" or (not echo_suppressed and confidence < session.min_barge_in_confidence):
            session.counters["echo_rejected_count"] += 1
            self._record_event(
                session,
                "voice.realtime.echo.rejected",
                {"voice_activity": voice_activity, "barge_in_confidence": confidence},
            )

    def _opened_payload(self, session: RealtimeVoiceSession) -> dict[str, Any]:
        """生成会话打开结果摘要。"""

        return {
            "session_id": session.session_id,
            "accepted_mode": session.accepted_mode,
            "transport": "websocket_media_frame",
            "capabilities": {
                "aec": session.capabilities.get("aec", "endpoint"),
                "vad": session.capabilities.get("vad", "endpoint"),
                "barge_in": session.capabilities.get("barge_in", True),
                "output_cancel": session.capabilities.get("output_cancel", True),
            },
        }

    def _record_event(self, session: RealtimeVoiceSession, name: str, payload: dict[str, Any]) -> None:
        """记录实时会话事件。"""

        event = {
            "name": name,
            "payload": payload,
            "created_at_ms": self._now_ms(),
        }
        session.recent_events.append(event)
        log_debug(
            self._logger,
            f"实时语音事件: {name}",
            LogContext(device_id=session.device_id, session_id=session.session_id, fields=payload),
        )

    def _session_snapshot(self, session: RealtimeVoiceSession) -> dict[str, Any]:
        """把单个实时会话转换为快照字典。"""

        active_input = session.active_input
        active_output = session.active_output
        return {
            "session_id": session.session_id,
            "requested_mode": session.requested_mode,
            "accepted_mode": session.accepted_mode,
            "realtime_state": session.state,
            "active_input_stream_id": active_input.input_stream_id if active_input else None,
            "active_output_stream_id": active_output.output_stream_id if active_output else None,
            "active_input": self._input_snapshot(active_input),
            "active_output": self._output_snapshot(active_output),
            "recent_realtime_events": list(session.recent_events),
            "recent_interrupts": list(session.recent_interrupts),
            "latency_metrics": dict(session.latency_metrics),
            "barge_in_count": session.counters["barge_in_count"],
            "echo_rejected_count": session.counters["echo_rejected_count"],
            "capabilities": dict(session.capabilities),
            "cancelled_output_stream_ids": sorted(session.cancelled_output_stream_ids),
        }

    @staticmethod
    def _input_snapshot(active_input: RealtimeInputStream | None) -> dict[str, Any] | None:
        """导出输入流快照。"""

        if active_input is None:
            return None
        return {
            "input_stream_id": active_input.input_stream_id,
            "reason": active_input.reason,
            "frame_count": active_input.frame_count,
            "bytes_received": active_input.bytes_received,
            "first_audio_at_ms": active_input.first_audio_at_ms,
            "committed_at_ms": active_input.committed_at_ms,
            "final_transcript": active_input.final_transcript,
            "finish_reason": active_input.finish_reason,
        }

    @staticmethod
    def _output_snapshot(active_output: RealtimeOutputStream | None) -> dict[str, Any] | None:
        """导出输出流快照。"""

        if active_output is None:
            return None
        return {
            "output_stream_id": active_output.output_stream_id,
            "intent_id": active_output.intent_id,
            "source": active_output.source,
            "priority": active_output.priority,
            "state": active_output.state,
            "chunk_count": active_output.chunk_count,
            "bytes_sent": active_output.bytes_sent,
            "cancel_reason": active_output.cancel_reason,
        }

    def _require_session(self, *, device_id: str, session_id: str) -> RealtimeVoiceSession:
        """读取实时会话并校验 session_id。"""

        session = self._sessions.get(device_id)
        if session is None:
            raise build_error(ErrorCode.STREAM_NOT_FOUND, "未找到实时语音会话", details={"device_id": device_id})
        if session.session_id != session_id:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "实时语音 session_id 不匹配",
                details={"expected_session_id": session.session_id, "actual_session_id": session_id},
            )
        return session

    @staticmethod
    def _latency_ms(start: int | None, end: int | None) -> int | None:
        """计算两个毫秒时间戳之间的延迟。"""

        if start is None or end is None:
            return None
        return max(0, end - start)

    @staticmethod
    def _now_ms() -> int:
        """返回当前毫秒时间戳。"""

        return int(time.time() * 1000)

    @staticmethod
    def _parse_float(value: Any, *, default: float) -> float:
        """把输入值安全转换为浮点数。"""

        try:
            return float(value)
        except (TypeError, ValueError):
            return default
