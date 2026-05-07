from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from audio_chat.observability import RunRecorder
from audio_chat.output import OutputService
from audio_chat.output.service import OutputItem
from audio_chat.protocol import StreamChunk, StreamFormat


@dataclass(frozen=True)
class RealtimeProviderConfig:
    """Realtime provider 配置。

    主要功能：保存 Omni Realtime 连接需要的最小参数。
    主要属性：`provider` 标识供应商，`model` 标识模型，`voice` 标识输出音色。
    """

    provider: str = "qwen"
    model: str = "qwen3.5-omni-plus-realtime"
    turn_detection: str = "provider"
    voice: str = "Tina"
    session_idle_timeout_seconds: int = 60
    websocket_url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
    instructions: str = "你是中文语音助手。请用简短口语回答用户。"


@dataclass(frozen=True)
class RealtimeProviderCallbacks:
    """Realtime provider 回调集合。

    主要功能：让 provider adapter 把模型事件交还给 RealtimeAudioAgentCore。
    主要属性：`audio_delta` 负责原生音频输出，`audio_done` 负责关闭当前输出，
    `provider_event` 负责记录 provider 原始事件摘要。
    """

    audio_delta: Callable[[bytes, StreamFormat, dict[str, Any]], None]
    audio_done: Callable[[dict[str, Any]], None]
    provider_event: Callable[[dict[str, Any]], None]
    error: Callable[[str, dict[str, Any]], None]


class RealtimeProviderAdapter(Protocol):
    """Realtime audio provider adapter 接口。

    主要功能：隔离 Qwen / Omni Realtime 协议，避免 Audio Pipeline 和 TextAgentCore
    持有外部 provider 细节。
    主要方法：`open()` 建立会话，`append_audio()` 持续追加 PCM，`cancel()` 和
    `close()` 分别处理用户打断与会话释放。
    """

    def open(self, *, user_id: str, session_id: str, callbacks: RealtimeProviderCallbacks) -> None:
        ...

    def append_audio(self, chunk: StreamChunk) -> None:
        ...

    def cancel(self, *, user_id: str, reason: str) -> None:
        ...

    def close(self, *, user_id: str, reason: str) -> None:
        ...


class QwenOmniRealtimeAdapter:
    """Qwen Omni Realtime provider adapter。

    主要功能：用 DashScope Omni Realtime 接收 16k PCM 麦克风流，并把 provider
    返回的 24k PCM audio delta 交给 SDK Output Service。
    主要属性：`config` 保存模型和音频配置，`_conversation` 保存 DashScope 会话对象。
    """

    def __init__(self, config: RealtimeProviderConfig | None = None) -> None:
        self.config = config or RealtimeProviderConfig()
        self._conversation: Any | None = None
        self._callbacks: RealtimeProviderCallbacks | None = None

    def open(self, *, user_id: str, session_id: str, callbacks: RealtimeProviderCallbacks) -> None:
        """建立 Omni Realtime 会话。

        主要逻辑：导入 DashScope SDK，创建 `OmniRealtimeConversation`，配置输入
        16k PCM、输出 24k PCM、audio/text 双模态和 provider turn detection。
        参数：`user_id`、`session_id` 用于日志关联；`callbacks` 用于上报模型事件。
        返回值：无。
        异常情况：缺少 `DASHSCOPE_API_KEY` 或 SDK 未安装时抛出 `RuntimeError`。
        """
        if self._conversation is not None:
            return
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            raise RuntimeError("DASHSCOPE_API_KEY is required for Qwen Omni Realtime")
        try:
            import dashscope
            from dashscope.audio.qwen_omni import (
                AudioFormat,
                MultiModality,
                OmniRealtimeCallback,
                OmniRealtimeConversation,
            )
        except ImportError as exc:  # pragma: no cover - exercised only in provider integration
            raise RuntimeError("dashscope package is required for Qwen Omni Realtime") from exc

        self._callbacks = callbacks
        adapter = self
        dashscope.api_key = api_key

        class _Callback(OmniRealtimeCallback):
            def on_open(self) -> None:  # pragma: no cover - provider callback
                callbacks.provider_event({"event": "omni.websocket.opened", "provider": "qwen"})

            def on_close(self, close_status_code: Any, close_msg: Any) -> None:  # pragma: no cover - provider callback
                callbacks.provider_event(
                    {
                        "event": "omni.websocket.closed",
                        "provider": "qwen",
                        "code": close_status_code,
                        "message": str(close_msg),
                    }
                )

            def on_event(self, message: dict[str, Any]) -> None:  # pragma: no cover - provider callback
                adapter._handle_provider_event(message)

        self._conversation = OmniRealtimeConversation(
            model=self.config.model,
            callback=_Callback(),
            url=self.config.websocket_url.rstrip("/"),
            api_key=api_key,
        )
        self._conversation.connect()
        turn_detection_type = "semantic_vad" if self.config.turn_detection in {"provider", "semantic_vad"} else "server_vad"
        self._conversation.update_session(
            output_modalities=[MultiModality.TEXT, MultiModality.AUDIO],
            voice=self.config.voice,
            input_audio_format=AudioFormat.PCM_16000HZ_MONO_16BIT,
            output_audio_format=AudioFormat.PCM_24000HZ_MONO_16BIT,
            enable_input_audio_transcription=True,
            input_audio_transcription_model="paraformer-realtime-v2",
            enable_turn_detection=True,
            turn_detection_type=turn_detection_type,
            instructions=self.config.instructions,
        )
        callbacks.provider_event(
            {
                "event": "omni.session.opened",
                "provider": "qwen",
                "model": self.config.model,
                "voice": self.config.voice,
                "input_audio": "pcm16le/16000/mono",
                "output_audio": "pcm16le/24000/mono",
                "turn_detection": turn_detection_type,
            }
        )

    def append_audio(self, chunk: StreamChunk) -> None:
        """追加一片麦克风 PCM。

        主要逻辑：不检查 `chunk.final`，直接把 payload base64 后交给 Omni。
        参数：`chunk` 为 Audio Pipeline 归一后的 sensor.mic StreamChunk。
        返回值：无。
        异常情况：provider 会话未打开时抛出 `RuntimeError`。
        """
        if self._conversation is None:
            raise RuntimeError("Realtime provider session is not opened")
        self._conversation.append_audio(base64.b64encode(chunk.payload).decode("ascii"))

    def cancel(self, *, user_id: str, reason: str) -> None:
        """取消当前 Omni 响应。

        主要逻辑：调用 provider cancel_response；provider 不支持或当前无响应时只记录错误回调。
        参数：`user_id`、`reason` 用于日志。
        返回值：无。
        异常情况：异常会转成 callbacks.error。
        """
        if self._conversation is None:
            return
        try:
            self._conversation.cancel_response()
        except Exception as exc:  # noqa: BLE001 - provider SDK may raise transport-specific errors
            if self._callbacks:
                self._callbacks.error(str(exc), {"event": "omni.response.cancel.failed", "reason": reason})

    def close(self, *, user_id: str, reason: str) -> None:
        """关闭 Omni Realtime 会话。

        主要逻辑：关闭 DashScope conversation，并清理本地引用。
        参数：`user_id`、`reason` 用于日志。
        返回值：无。
        异常情况：异常会转成 callbacks.error。
        """
        if self._conversation is None:
            return
        try:
            self._conversation.close()
        except Exception as exc:  # noqa: BLE001
            if self._callbacks:
                self._callbacks.error(str(exc), {"event": "omni.session.close.failed", "reason": reason})
        finally:
            self._conversation = None

    def _handle_provider_event(self, message: dict[str, Any]) -> None:
        callbacks = self._callbacks
        if callbacks is None:
            return
        event_type = str(message.get("type") or "")
        if event_type == "response.audio.delta":
            raw_delta = str(message.get("delta") or "")
            callbacks.provider_event(
                {
                    "event": "omni.response.audio.delta",
                    "provider": "qwen",
                    "delta_base64_len": len(raw_delta),
                }
            )
            if raw_delta:
                audio = base64.b64decode(raw_delta)
                callbacks.provider_event(
                    {
                        "event": "omni.response.audio.delta.decoded",
                        "provider": "qwen",
                        "audio_bytes": len(audio),
                    }
                )
                callbacks.audio_delta(
                    audio,
                    StreamFormat(codec="pcm16le", sample_rate=24000, channels=1, chunk_ms=20),
                    {
                        "provider": "qwen",
                        "model": self.config.model,
                        "voice": self.config.voice,
                        "provider_event": event_type,
                    },
                )
            return
        if event_type == "response.audio.done":
            callbacks.provider_event({"event": "omni.response.audio.done", "provider": "qwen"})
            callbacks.audio_done({"provider": "qwen", "model": self.config.model, "provider_event": event_type})
            return
        if event_type == "error":
            callbacks.error(str(message.get("message") or message), {"provider": "qwen", "raw": message})
            return
        callbacks.provider_event(_summarize_omni_event(message))


class RealtimeOutputAdapter:
    """Realtime 输出适配器。

    主要功能：把 provider 的原生 audio delta 转换为 Output Service 的 native audio
    输入，不经过 TextAgentCore 或 TTS。
    主要方法：`emit_audio_delta()` 写入音频，`emit_audio_done()` 关闭当前 output stream。
    """

    def __init__(self, *, output_service: OutputService, recorder: RunRecorder) -> None:
        self.output_service = output_service
        self.recorder = recorder

    def emit_audio_delta(
        self,
        *,
        user_id: str,
        session_id: str,
        audio: bytes,
        format: StreamFormat,
        metadata: dict[str, Any],
    ) -> None:
        """下发一片 provider 原生音频。

        主要逻辑：构造内部输出意图，交给 Output Service 打开或复用
        actuator.speaker stream。
        参数：`audio` 为 PCM payload，`format` 为 provider 输出格式。
        返回值：无。
        异常情况：由 Output Service 抛出的异常继续向上传递。
        """
        self.output_service.on_assistant_audio_delta(
            user_id=user_id,
            session_id=session_id,
            audio=audio,
            format=format,
            final=False,
            intent=OutputItem(user_id=user_id, session_id=session_id, source="realtime_audio", priority="normal"),
            metadata=metadata,
        )

    def emit_audio_done(self, *, user_id: str, session_id: str, metadata: dict[str, Any]) -> None:
        """结束当前 provider 原生音频输出。

        主要逻辑：发送 final=True 的空音频，让 Output Service 关闭对应 output stream。
        参数：`metadata` 记录 provider 和模型信息。
        返回值：无。
        异常情况：由 Output Service 抛出的异常继续向上传递。
        """
        self.output_service.on_assistant_audio_delta(
            user_id=user_id,
            session_id=session_id,
            audio=b"",
            format=StreamFormat(codec="pcm16le", sample_rate=24000, channels=1, chunk_ms=20),
            final=True,
            intent=OutputItem(user_id=user_id, session_id=session_id, source="realtime_audio", priority="normal"),
            metadata=metadata,
        )


class RealtimeAudioAgentCore:
    """Realtime audio agent core 最小实现。

    主要功能：接收 Audio Pipeline 的 sensor.mic PCM chunk，直连 Omni Realtime provider，
    并把 provider 返回的 response.audio.delta 原生下发到 actuator.speaker。
    主要属性：`provider_factory` 支持单元测试注入 fake adapter，`_sessions` 记录每个
    user 的 realtime provider session。
    """

    def __init__(
        self,
        *,
        output_service: OutputService,
        recorder: RunRecorder,
        realtime_config: RealtimeProviderConfig | None = None,
        provider_factory: Callable[[RealtimeProviderConfig], RealtimeProviderAdapter] | None = None,
        **_: Any,
    ) -> None:
        self.output_service = output_service
        self.recorder = recorder
        self.realtime_config = realtime_config or RealtimeProviderConfig()
        self.provider_factory = provider_factory or self._default_provider_factory
        self.output_adapter = RealtimeOutputAdapter(output_service=output_service, recorder=recorder)
        self._sessions: dict[str, tuple[str, RealtimeProviderAdapter]] = {}
        self._failed_sessions: set[str] = set()

    def open(self, user_id: str, session_id: str) -> None:
        """打开用户 realtime provider 会话。

        主要逻辑：同一 user 已有同 session 时复用；session 变化时先关闭旧会话，再打开新会话。
        参数：`user_id` 为用户标识，`session_id` 为音频会话标识。
        返回值：无。
        异常情况：provider 初始化失败时抛出异常，并记录 `system.error.raised`。
        """
        existing = self._sessions.get(user_id)
        if existing and existing[0] == session_id:
            return
        if existing:
            existing[1].close(user_id=user_id, reason="session_replaced")
        self._failed_sessions.discard(session_id)
        provider = self.provider_factory(self.realtime_config)
        callbacks = self._callbacks(user_id=user_id, session_id=session_id)
        try:
            provider.open(user_id=user_id, session_id=session_id, callbacks=callbacks)
        except Exception as exc:
            self.recorder.record_system_event(
                {
                    "event": "system.error.raised",
                    "component": "RealtimeAudioAgentCore",
                    "reason": str(exc),
                    "user_id": user_id,
                    "session_id": session_id,
                }
            )
            raise
        self._sessions[user_id] = (session_id, provider)

    def append_audio_event(self, chunk: StreamChunk) -> None:
        """追加 Audio Pipeline 归一后的 sensor.mic chunk。

        主要逻辑：按 chunk 的 user/session 自动打开 provider 会话，然后立即 append 音频；
        不等待 `chunk.final`，turn 判断交给 Omni Realtime。
        参数：`chunk` 为 sensor.mic StreamChunk。
        返回值：无。
        异常情况：非 sensor.mic 或 provider append 失败时抛出异常。
        """
        if chunk.stream_type != "sensor.mic":
            raise ValueError("RealtimeAudioAgentCore only accepts sensor.mic")
        if chunk.session_id in self._failed_sessions:
            return
        self.open(chunk.user_id, chunk.session_id)
        _session_id, provider = self._sessions[chunk.user_id]
        try:
            provider.append_audio(chunk)
        except Exception as exc:
            self._mark_session_failed(
                user_id=chunk.user_id,
                session_id=chunk.session_id,
                message=str(exc),
                record={"event": "realtime.provider.append_audio.failed"},
            )
            return
        self.recorder.record_agent_event(
            chunk.session_id,
            {
                "event": "realtime.input_audio.appended",
                "provider": self.realtime_config.provider,
                "model": self.realtime_config.model,
                "payload_size": len(chunk.payload),
                "final": chunk.final,
                "duration_ms": chunk.duration_ms,
            },
        )

    def interrupt(self, user_id: str, reason: str) -> None:
        """处理用户打断。

        主要逻辑：取消 provider 当前响应，同时取消 Output Service 当前 output stream。
        参数：`user_id` 为用户标识，`reason` 为打断原因。
        返回值：无。
        异常情况：provider cancel 异常由 adapter 转成错误事件。
        """
        existing = self._sessions.get(user_id)
        session_id = existing[0] if existing else None
        if existing:
            existing[1].cancel(user_id=user_id, reason=reason)
        self.output_service.interrupt_user(user_id, session_id=session_id, reason=reason)
        self.recorder.record_agent_event(
            session_id or "realtime-interruptions",
            {"event": "realtime.response.cancelled", "user_id": user_id, "reason": reason},
        )

    def close(self, user_id: str, reason: str) -> None:
        """关闭用户 realtime 会话。

        主要逻辑：释放 provider 会话，并取消可能仍在播放的 output stream。
        参数：`user_id` 为用户标识，`reason` 为关闭原因。
        返回值：无。
        异常情况：provider close 异常由 adapter 转成错误事件。
        """
        existing = self._sessions.pop(user_id, None)
        session_id = existing[0] if existing else None
        if existing:
            existing[1].close(user_id=user_id, reason=reason)
        if session_id:
            self._failed_sessions.discard(session_id)
        self.output_service.interrupt_user(user_id, session_id=session_id, reason=reason)
        if session_id:
            self.recorder.record_agent_event(session_id, {"event": "realtime.session.closed", "reason": reason})

    def _callbacks(self, *, user_id: str, session_id: str) -> RealtimeProviderCallbacks:
        return RealtimeProviderCallbacks(
            audio_delta=lambda audio, fmt, metadata: self.output_adapter.emit_audio_delta(
                user_id=user_id,
                session_id=session_id,
                audio=audio,
                format=fmt,
                metadata=metadata,
            ),
            audio_done=lambda metadata: self.output_adapter.emit_audio_done(
                user_id=user_id,
                session_id=session_id,
                metadata=metadata,
            ),
            provider_event=lambda record: self.recorder.record_agent_event(session_id, record),
            error=lambda message, record: self._mark_session_failed(
                user_id=user_id,
                session_id=session_id,
                message=message,
                record=record,
            ),
        )

    def _default_provider_factory(self, config: RealtimeProviderConfig) -> RealtimeProviderAdapter:
        if config.provider == "qwen":
            return QwenOmniRealtimeAdapter(config)
        raise ValueError(f"unsupported realtime provider: {config.provider}")

    def _mark_session_failed(self, *, user_id: str, session_id: str, message: str, record: dict[str, Any]) -> None:
        """标记当前 realtime 会话失败。

        主要逻辑：provider 一旦报错或连接关闭，后续 mic chunk 不再继续 append，避免
        `/ws/stream` 对同一个错误持续刷屏。
        参数：`user_id`、`session_id` 定位会话；`message` 和 `record` 写入 runs。
        返回值：无。
        异常情况：无。
        """
        first_failure = session_id not in self._failed_sessions
        self._failed_sessions.add(session_id)
        existing = self._sessions.pop(user_id, None)
        if existing:
            try:
                existing[1].close(user_id=user_id, reason="provider_failed")
            except Exception:
                pass
        if first_failure:
            self.recorder.record_system_event(
                {
                    "event": "system.error.raised",
                    "component": "RealtimeProviderAdapter",
                    "message": message,
                    "user_id": user_id,
                    "session_id": session_id,
                    **record,
                }
            )
            self.recorder.record_agent_event(
                session_id,
                {
                    "event": "realtime.session.failed",
                    "provider": self.realtime_config.provider,
                    "model": self.realtime_config.model,
                    "message": message,
                },
            )


def _summarize_omni_event(message: dict[str, Any]) -> dict[str, Any]:
    event_type = str(message.get("type") or "unknown")
    record: dict[str, Any] = {"event": f"omni.{event_type}", "provider": "qwen"}
    if event_type == "response.audio_transcript.delta":
        record["delta"] = message.get("delta")
    elif event_type == "response.audio_transcript.done":
        record["transcript"] = message.get("transcript")
    elif event_type == "conversation.item.input_audio_transcription.completed":
        record["transcript"] = message.get("transcript")
    elif event_type == "response.done":
        response = message.get("response") if isinstance(message.get("response"), dict) else {}
        record["status"] = response.get("status")
    return record
