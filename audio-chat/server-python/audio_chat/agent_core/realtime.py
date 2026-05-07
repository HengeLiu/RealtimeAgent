from __future__ import annotations

import asyncio
import base64
import os
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from audio_chat.agent_core.base import AgentEventBuffer, AgentCoreEvent
from audio_chat.observability import RunRecorder
from audio_chat.output import OutputService
from audio_chat.output.service import OutputItem
from audio_chat.protocol import StreamChunk, StreamFormat
from audio_chat.tools import ToolGateway


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

    def commit_input(self, *, user_id: str, session_id: str, reason: str) -> None:
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

    def commit_input(self, *, user_id: str, session_id: str, reason: str) -> None:
        """提交 provider 输入边界。

        主要逻辑：Qwen Omni 当前主要由 provider turn detection 决定边界；
        如果 SDK 版本暴露 commit 方法则调用，否则只记录为 no-op。
        参数：`user_id`、`session_id` 用于关联日志；`reason` 为提交原因。
        返回值：无。
        异常情况：provider 异常会转成 callbacks.error。
        """

        if self._conversation is None:
            return
        try:
            commit = getattr(self._conversation, "commit_input_audio", None)
            if callable(commit):
                commit()
        except Exception as exc:  # noqa: BLE001
            if self._callbacks:
                self._callbacks.error(str(exc), {"event": "omni.input.commit.failed", "reason": reason})

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


class MockRealtimeProviderAdapter:
    """稳定 mock realtime provider。

    主要功能：
    1. 不访问网络，供 `agent.mode=realtime_audio` 的单元测试和本地预检使用。
    2. 每次收到输入音频后回调一片原生音频 delta。
    3. `commit_input` 时回调 audio done，模拟 provider 完成一轮响应。

    主要属性：
    1. `config`：provider 配置。
    2. `_callbacks`：RealtimeAudioAgentCore 注入的回调集合。
    3. `appended`：测试可读取的输入 chunk 列表。
    """

    def __init__(self, config: RealtimeProviderConfig | None = None) -> None:
        self.config = config or RealtimeProviderConfig(provider="mock", model="mock-realtime")
        self._callbacks: RealtimeProviderCallbacks | None = None
        self.appended: list[StreamChunk] = []
        self.cancelled = False
        self.closed = False

    def open(self, *, user_id: str, session_id: str, callbacks: RealtimeProviderCallbacks) -> None:
        """打开 mock 会话。"""

        self._callbacks = callbacks
        callbacks.provider_event(
            {
                "event": "mock_realtime.session.opened",
                "provider": "mock",
                "model": self.config.model,
            }
        )

    def append_audio(self, chunk: StreamChunk) -> None:
        """接收音频并生成一片 mock 原生音频。"""

        if self._callbacks is None:
            raise RuntimeError("mock realtime session is not opened")
        self.appended.append(chunk)
        if chunk.payload:
            self._callbacks.audio_delta(
                b"\x01\x00" * 320,
                StreamFormat(codec="pcm16le", sample_rate=16000, channels=1, chunk_ms=20),
                {"provider": "mock", "model": self.config.model},
            )
        if chunk.final:
            self.commit_input(user_id=chunk.user_id, session_id=chunk.session_id, reason="final_chunk")

    def commit_input(self, *, user_id: str, session_id: str, reason: str) -> None:
        """提交 mock 输入边界并结束音频输出。"""

        if self._callbacks is None:
            return
        self._callbacks.provider_event(
            {
                "event": "mock_realtime.input.committed",
                "provider": "mock",
                "reason": reason,
            }
        )
        self._callbacks.audio_done({"provider": "mock", "model": self.config.model, "reason": reason})

    def cancel(self, *, user_id: str, reason: str) -> None:
        """记录取消状态。"""

        self.cancelled = True

    def close(self, *, user_id: str, reason: str) -> None:
        """关闭 mock 会话。"""

        self.closed = True


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


class RealtimeToolBridge:
    """Realtime provider 工具桥。

    主要功能：把 ToolGateway 暴露为 provider function calling schema，并聚合
    realtime tool call 参数后调用工具，再把 ToolResult 转成 provider 可回填结构。
    主要方法：`tool_schemas()`、`append_tool_call_delta()`、`commit_tool_call()`。
    """

    def __init__(self, *, tool_gateway: ToolGateway | None = None, recorder: RunRecorder | None = None) -> None:
        self.tool_gateway = tool_gateway
        self.recorder = recorder
        self._pending: dict[str, dict[str, Any]] = {}

    def bind_tool_gateway(self, tool_gateway: ToolGateway) -> None:
        """绑定工具网关。"""

        self.tool_gateway = tool_gateway

    def tool_schemas(self) -> list[dict]:
        """返回 provider 可用的工具 schema。"""

        return self.tool_gateway.provider_schemas() if self.tool_gateway is not None else []

    def append_tool_call_delta(
        self,
        *,
        tool_call_id: str,
        name: str | None = None,
        arguments_delta: dict | None = None,
    ) -> None:
        """聚合 provider 工具调用参数增量。"""

        record = self._pending.setdefault(tool_call_id, {"name": name or "", "arguments": {}})
        if name:
            record["name"] = name
        record["arguments"].update(arguments_delta or {})

    def commit_tool_call(self, *, tool_call_id: str, user_id: str, session_id: str) -> dict:
        """提交完整工具调用并返回 provider 回填数据。"""

        if self.tool_gateway is None:
            return {"tool_call_id": tool_call_id, "ok": False, "error": {"message": "tool gateway is not configured"}}
        record = self._pending.pop(tool_call_id, {"name": "", "arguments": {}})
        name = str(record.get("name") or "")
        arguments = dict(record.get("arguments") or {})
        if self.recorder is not None:
            self.recorder.record_agent_event(
                session_id,
                {"event": "realtime.tool_call.committed", "tool_call_id": tool_call_id, "tool_name": name},
            )
        result = asyncio.run(
            self.tool_gateway.call(
                name=name,
                user_id=user_id,
                session_id=session_id,
                input_data=arguments,
            )
        )
        return {
            "tool_call_id": tool_call_id,
            "name": name,
            "ok": result.ok,
            "data": result.data,
            "message": result.message,
            "error": result.error,
            "meta": result.meta or {},
        }


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
        tool_gateway: ToolGateway | None = None,
        **_: Any,
    ) -> None:
        self.output_service = output_service
        self.recorder = recorder
        self.realtime_config = realtime_config or RealtimeProviderConfig()
        self.provider_factory = provider_factory or self._default_provider_factory
        self.output_adapter = RealtimeOutputAdapter(output_service=output_service, recorder=recorder)
        self.tool_bridge = RealtimeToolBridge(tool_gateway=tool_gateway, recorder=recorder)
        self._sessions: dict[str, tuple[str, RealtimeProviderAdapter]] = {}
        self._failed_sessions: set[str] = set()
        self._event_buffer = AgentEventBuffer()

    def bind_tool_gateway(self, tool_gateway: ToolGateway) -> None:
        """绑定 Realtime provider 工具桥使用的 ToolGateway。"""

        self.tool_bridge.bind_tool_gateway(tool_gateway)

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
            self._record_system_error(
                user_id=user_id,
                session_id=session_id,
                message=str(exc),
                record={"event": "realtime.provider.open.failed", "component": "RealtimeAudioAgentCore"},
            )
            raise
        self._sessions[user_id] = (session_id, provider)
        self._record_event(
            "session.opened",
            user_id=user_id,
            session_id=session_id,
            provider=self.realtime_config.provider,
            model=self.realtime_config.model,
        )

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
        self._event_buffer.record_event(
            "input_audio.appended",
            user_id=chunk.user_id,
            session_id=chunk.session_id,
            payload={"payload_size": len(chunk.payload), "final": chunk.final},
        )

    def commit_input(self, user_id: str, session_id: str, *, reason: str = "endpoint_commit") -> None:
        """提交 realtime provider 输入边界。

        主要逻辑：把显式输入提交转发给当前 provider；provider 不支持时记录降级事件。
        参数：`user_id`、`session_id` 定位会话；`reason` 为提交来源。
        返回值：无。
        异常情况：provider 异常转为 system error 和 agent event，不向上刷屏。
        """

        existing = self._sessions.get(user_id)
        if not existing or existing[0] != session_id:
            self._record_event("input.commit.skipped", user_id=user_id, session_id=session_id, reason="session_not_open")
            return
        provider = existing[1]
        try:
            commit = getattr(provider, "commit_input", None)
            if callable(commit):
                commit(user_id=user_id, session_id=session_id, reason=reason)
            else:
                self.recorder.record_system_event(
                    {
                        "event": "system.degradation.raised",
                        "component": "RealtimeProviderAdapter",
                        "reason": "provider does not implement commit_input",
                        "user_id": user_id,
                        "session_id": session_id,
                    }
                )
        except Exception as exc:  # noqa: BLE001
            self._mark_session_failed(
                user_id=user_id,
                session_id=session_id,
                message=str(exc),
                record={"event": "realtime.provider.commit_input.failed"},
            )
            return
        self._record_event("input.committed", user_id=user_id, session_id=session_id, reason=reason)

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
        self._event_buffer.record_event(
            "response.cancelled",
            user_id=user_id,
            session_id=session_id or "",
            payload={"reason": reason},
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
        self._event_buffer.record_event(
            "session.closed",
            user_id=user_id,
            session_id=session_id or "",
            payload={"reason": reason},
        )

    def events(self) -> list[AgentCoreEvent]:
        """返回 realtime Agent 统一事件快照。"""

        return self._event_buffer.events()

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
            provider_event=lambda record: self._record_provider_event(
                user_id=user_id,
                session_id=session_id,
                record=record,
            ),
            error=lambda message, record: self._mark_session_failed(
                user_id=user_id,
                session_id=session_id,
                message=message,
                record=record,
            ),
        )

    def _default_provider_factory(self, config: RealtimeProviderConfig) -> RealtimeProviderAdapter:
        if config.provider == "mock":
            return MockRealtimeProviderAdapter(config)
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
            self._record_system_error(
                user_id=user_id,
                session_id=session_id,
                message=message,
                record={"component": "RealtimeProviderAdapter", **record},
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
            self._event_buffer.record_event(
                "session.error",
                user_id=user_id,
                session_id=session_id,
                payload={"message": message, **record},
            )

    def _record_provider_event(self, *, user_id: str, session_id: str, record: dict[str, Any]) -> None:
        """记录 provider 事件到 runs 和统一事件缓存。"""

        self.recorder.record_agent_event(session_id, record)
        self._event_buffer.record_event(
            str(record.get("event") or "provider.event"),
            user_id=user_id,
            session_id=session_id,
            payload=dict(record),
        )

    def _record_event(self, event: str, *, user_id: str, session_id: str, **payload) -> None:
        """记录统一 Agent 事件到内存和 runs。"""

        self._event_buffer.record_event(event, user_id=user_id, session_id=session_id, payload=payload)
        self.recorder.record_agent_event(session_id, {"event": event, "user_id": user_id, **payload})

    def _record_system_error(
        self,
        *,
        user_id: str,
        session_id: str,
        message: str,
        record: dict[str, Any],
    ) -> None:
        """写入 provider/system 错误，避免异常在热路径重复刷屏。"""

        self.recorder.record_system_event(
            {
                "event": "system.error.raised",
                "message": message,
                "user_id": user_id,
                "session_id": session_id,
                **record,
            }
        )
        self._event_buffer.record_event(
            "session.error",
            user_id=user_id,
            session_id=session_id,
            payload={"message": message, **record},
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
