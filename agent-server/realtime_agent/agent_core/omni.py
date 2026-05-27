from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Protocol

from realtime_agent.agent_core.base import AgentEventBuffer, AgentCoreEvent
from realtime_agent.agent_core.context import ContextCompileRequest, ContextCompiler, PromptRegistry, record_context_events
from realtime_agent.agent_core.recovery import DEFAULT_RECOVERABLE_ERROR_MESSAGE, record_agent_recovery_error
from realtime_agent.agent_core.visual import OmniVisualAppender, VisualAppendContext
from realtime_agent.asset.service import AssetService
from realtime_agent.control import ControlService
from realtime_agent.observability import RunRecorder
from realtime_agent.output import OutputService
from realtime_agent.output.service import OutputItem
from realtime_agent.protocol import SERVER_PRODUCER_ID, Event, StreamChunk, StreamFormat, new_id
from realtime_agent.tools import ToolGateway

REALTIME_INLINE_VISION_TOOLS = {"capture_photo", "interpret_current_view", "interpret_image"}
OMNI_REALTIME_IMAGE_MAX_BYTES = 180_000
DEFAULT_REALTIME_PROVIDER_MAX_CONCURRENT_SESSIONS = 10


class RealtimeProviderConcurrencyLimitError(RuntimeError):
    """Realtime provider 并发连接数超过限制。

    主要功能：在真实 provider 连接数达到上限时给出明确错误，避免继续建立
    WebSocket 连接触发供应商限流。
    主要方法：继承 `RuntimeError`，由 provider adapter 在 `open()` 阶段抛出。
    主要属性：无。
    """


_REALTIME_PROVIDER_LIMITERS: dict[tuple[str, str, str], tuple[int, threading.BoundedSemaphore]] = {}
_REALTIME_PROVIDER_LIMITERS_LOCK = threading.Lock()


def _normalize_realtime_provider_max_concurrent_sessions(value: int | None) -> int:
    """归一化 realtime provider 最大并发会话数。

    主要逻辑：缺省或非法非正值都回退到 SDK 默认值，确保真实 provider 不会在
    未配置时无限制创建并发连接。
    参数：`value` 为配置中读取的并发上限。
    返回值：大于 0 的并发上限。
    异常情况：无。
    """

    try:
        limit = int(value or DEFAULT_REALTIME_PROVIDER_MAX_CONCURRENT_SESSIONS)
    except (TypeError, ValueError):
        return DEFAULT_REALTIME_PROVIDER_MAX_CONCURRENT_SESSIONS
    return limit if limit > 0 else DEFAULT_REALTIME_PROVIDER_MAX_CONCURRENT_SESSIONS


def _realtime_provider_limiter(
    *, provider: str, model: str, websocket_url: str, max_concurrent_sessions: int
) -> threading.BoundedSemaphore:
    """按 provider / model / endpoint 获取进程内并发 limiter。

    主要逻辑：同一个 Python 进程内，相同 provider 目标共享一个 BoundedSemaphore；
    测试或应用若切换到不同模型 / endpoint，则互不影响。
    参数：`provider`、`model`、`websocket_url` 描述 provider 目标，`max_concurrent_sessions`
    为并发上限。
    返回值：可用于 acquire / release 的信号量。
    异常情况：无。
    """

    limit = _normalize_realtime_provider_max_concurrent_sessions(max_concurrent_sessions)
    key = (str(provider or "").strip(), str(model or "").strip(), str(websocket_url or "").strip().rstrip("/"))
    with _REALTIME_PROVIDER_LIMITERS_LOCK:
        existing = _REALTIME_PROVIDER_LIMITERS.get(key)
        if existing is not None:
            return existing[1]
        semaphore = threading.BoundedSemaphore(limit)
        _REALTIME_PROVIDER_LIMITERS[key] = (limit, semaphore)
        return semaphore


def _registered_prompt_text(name: str, fallback: str) -> str:
    """读取已注册 prompt，失败时使用 fallback。"""

    asset = PromptRegistry().maybe_get(name)
    return asset.content if asset is not None else fallback


REALTIME_TOOL_CALL_PROMPT_RULE = _registered_prompt_text(
    "omni_tool_call_rules",
    (
        "当用户请求需要调用工具、启动后台任务、查询设备或执行动作时，必须直接调用合适的工具；"
        "在工具调用完成并收到工具结果前，不要先向用户播报“我要调用工具”“正在调用工具”“请稍等”等提示音频。"
        "永远不要向用户朗读工具名称、函数名、参数、JSON、schema、调用过程或系统实现细节。"
        "工具结果返回后，再用简短自然中文说明用户真正需要知道的结果。"
    ),
)


def _normalize_history_message(record: dict[str, Any]) -> dict[str, Any] | None:
    """把落盘消息转换为 Realtime 可注入的历史上下文。"""

    role = str(record.get("role") or "").strip()
    if role not in {"user", "assistant"}:
        return None
    content = record.get("content")
    text = " ".join(content.strip().split()) if isinstance(content, str) else ""
    if not text:
        return None
    return {"role": role, "content": text}


def _provider_tool_schema_name(schema: dict[str, Any]) -> str:
    """从 provider tool schema 中提取工具名。

    主要逻辑：兼容 OpenAI-compatible 的 `function.name` 和 Realtime 扁平
    `name` 两种结构。
    参数：`schema` 为工具 schema。
    返回值：工具名；缺失时返回空字符串。
    异常情况：无。
    """

    function = schema.get("function") if isinstance(schema, dict) else None
    if isinstance(function, dict):
        return str(function.get("name") or "")
    return str(schema.get("name") or "") if isinstance(schema, dict) else ""


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
    prompt: str = (
        "你是中文语音助手。请用简短口语回答用户。"
        "历史助手消息中如果出现 `<用户打断>`，表示标记前的内容用户可能已经听到，"
        "标记后的内容是系统已经生成但未继续播报给用户的上下文，只能作为后续回答参考。"
    )
    tools: list[dict[str, Any]] = field(default_factory=list)
    realtime_video_enabled: bool = True
    visual_frame_interval_seconds: float = 1.0
    visual_frame_timeout_seconds: float = 1.5
    visual_frame_ttl_seconds: float = 5.0
    visual_max_frames_per_turn: int = 8
    visual_direction: str = "front"
    max_concurrent_sessions: int = DEFAULT_REALTIME_PROVIDER_MAX_CONCURRENT_SESSIONS


@dataclass(frozen=True)
class RealtimeProviderCallbacks:
    """Realtime provider 回调集合。

    主要功能：让 provider adapter 把模型事件交还给 OmniRealtimeAgentCore。
    主要属性：`audio_delta` 负责原生音频输出，`audio_done` 负责关闭当前输出，
    `provider_event` 负责记录 provider 原始事件摘要。
    """

    audio_delta: Callable[[bytes, StreamFormat, dict[str, Any]], None]
    audio_done: Callable[[dict[str, Any]], None]
    provider_event: Callable[[dict[str, Any]], None]
    error: Callable[[str, dict[str, Any]], None]
    tool_call_delta: Callable[[dict[str, Any]], None] | None = None
    tool_call_done: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    replay_audio_for_tool_result: Callable[[dict[str, Any]], list[bytes]] | None = None


class RealtimeProviderAdapter(Protocol):
    """Realtime audio provider adapter 接口。

    主要功能：隔离 Qwen / Omni Realtime 协议，避免 Audio Pipeline 和 VisionRealtimeAgentCore
    持有外部 provider 细节。
    主要方法：`open()` 建立会话，`append_audio()` 持续追加 PCM，`cancel()` 和
    `close()` 分别处理用户打断与会话释放。
    """

    def open(self, *, user_id: str, session_id: str, callbacks: RealtimeProviderCallbacks) -> None:
        ...

    def append_audio(self, chunk: StreamChunk) -> None:
        ...

    def append_image(self, image: bytes, *, user_id: str, session_id: str, metadata: dict[str, Any] | None = None) -> None:
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
        self._output_modalities: list[Any] = []
        self._completed_tool_call_ids: set[str] = set()
        self._pending_tool_followup_response: dict[str, Any] | None = None
        self._current_response_audio_emitted = False
        self._session_updated = threading.Event()
        self._operation_lock = threading.RLock()
        self._provider_limiter: threading.BoundedSemaphore | None = None
        self._provider_slot_acquired = False

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
        self._completed_tool_call_ids.clear()
        self._session_updated.clear()
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
        self._acquire_provider_slot(user_id=user_id, session_id=session_id, callbacks=callbacks)

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

        try:
            self._conversation = OmniRealtimeConversation(
                model=self.config.model,
                callback=_Callback(),
                url=self.config.websocket_url.rstrip("/"),
                api_key=api_key,
            )
            self._conversation.connect()
            turn_detection_type = "semantic_vad" if self.config.turn_detection in {"provider", "semantic_vad"} else "server_vad"
            self._output_modalities = [MultiModality.TEXT, MultiModality.AUDIO]
            session_update_kwargs: dict[str, Any] = {
                "output_modalities": self._output_modalities,
                "voice": self.config.voice,
                "input_audio_format": AudioFormat.PCM_16000HZ_MONO_16BIT,
                "output_audio_format": AudioFormat.PCM_24000HZ_MONO_16BIT,
                "enable_input_audio_transcription": True,
                "input_audio_transcription_model": "paraformer-realtime-v2",
                "enable_turn_detection": True,
                "turn_detection_type": turn_detection_type,
                "instructions": self.config.prompt,
            }
            if self.config.tools:
                session_update_kwargs["tools"] = self.config.tools
            with self._operation_lock:
                self._conversation.update_session(**session_update_kwargs)
            session_updated = self._session_updated.wait(timeout=5)
            callbacks.provider_event(
                {
                    "event": "omni.session.opened",
                    "provider": "qwen",
                    "model": self.config.model,
                    "voice": self.config.voice,
                    "input_audio": "pcm16le/16000/mono",
                    "output_audio": "pcm16le/24000/mono",
                    "turn_detection": turn_detection_type,
                    "tool_count": len(self.config.tools),
                    "session_updated": session_updated,
                    "max_concurrent_sessions": _normalize_realtime_provider_max_concurrent_sessions(
                        self.config.max_concurrent_sessions
                    ),
                }
            )
        except Exception:
            self._conversation = None
            self._release_provider_slot()
            raise

    def append_audio(self, chunk: StreamChunk) -> None:
        """追加一片麦克风 PCM。

        主要逻辑：普通 chunk 直接把 payload base64 后交给 Omni；endpoint 发送
        `final=true` 时同步提交输入边界，确保图片和工具回填能进入下一轮响应。
        参数：`chunk` 为 Audio Pipeline 归一后的 sensor.mic StreamChunk。
        返回值：无。
        异常情况：provider 会话未打开时抛出 `RuntimeError`。
        """
        if self._conversation is None:
            raise RuntimeError("Realtime provider session is not opened")
        with self._operation_lock:
            if chunk.payload:
                self._conversation.append_audio(base64.b64encode(chunk.payload).decode("ascii"))
        if chunk.final:
            self.commit_input(user_id=chunk.user_id, session_id=chunk.session_id, reason="final_chunk")

    def append_image(self, image: bytes, *, user_id: str, session_id: str, metadata: dict[str, Any] | None = None) -> None:
        """向当前 Omni Realtime turn 追加一张图片。

        主要逻辑：把 server 侧刚收到的 `sensor.rgb` JPEG bytes 通过 DashScope
        `append_video` 追加到同一条 Realtime 会话。发送前会按 Omni WebSocket
        单帧限制压缩，避免 base64 后超过 provider frame 上限。
        参数：`image` 为 JPEG/PNG bytes；`metadata` 用于日志诊断。
        返回值：无。
        异常情况：provider 未打开或 SDK 不支持图片追加时抛出异常。
        """

        if self._conversation is None:
            raise RuntimeError("Realtime provider session is not opened")
        append_video = getattr(self._conversation, "append_video", None)
        if not callable(append_video):
            raise RuntimeError("Realtime provider conversation does not support append_video")
        prepared_image, image_metadata = _prepare_omni_realtime_image(image)
        with self._operation_lock:
            append_video(base64.b64encode(prepared_image).decode("ascii"))
        if self._callbacks:
            self._callbacks.provider_event(
                {
                    "event": "omni.input_image_buffer.appended",
                    "provider": "qwen",
                    "image_bytes": len(prepared_image),
                    "image_sha256": hashlib.sha256(prepared_image).hexdigest(),
                    **dict(metadata or {}),
                    **image_metadata,
                }
            )

    def commit_input(self, *, user_id: str, session_id: str, reason: str) -> None:
        """提交 provider 输入边界。

        主要逻辑：Qwen Omni 连续麦克风主要由 provider turn detection 决定边界；
        端侧明确上传 `final=true` 时只调用 commit，不额外创建 response，避免
        与 provider 自动回合响应重复。
        参数：`user_id`、`session_id` 用于关联日志；`reason` 为提交原因。
        返回值：无。
        异常情况：provider 异常会转成 callbacks.error。
        """

        if self._conversation is None:
            return
        try:
            commit = getattr(self._conversation, "commit", None)
            if not callable(commit):
                commit = getattr(self._conversation, "commit_input_audio", None)
            if callable(commit):
                with self._operation_lock:
                    commit()
                if self._callbacks:
                    self._callbacks.provider_event(
                        {
                            "event": "omni.input.committed",
                            "provider": "qwen",
                            "reason": reason,
                        }
                    )
            elif self._callbacks:
                self._callbacks.provider_event(
                    {
                        "event": "omni.input.commit.skipped",
                        "provider": "qwen",
                        "reason": reason,
                        "message": "provider conversation has no commit method",
                    }
                )
        except Exception as exc:  # noqa: BLE001
            if self._callbacks:
                self._callbacks.error(str(exc), {"event": "omni.input.commit.failed", "reason": reason})

    def cancel(self, *, user_id: str, reason: str) -> None:
        """取消当前 Omni 响应。

        主要逻辑：调用 provider cancel_response；provider 不支持或当前无响应时只记录
        provider 事件，不把正常打断路径升级成会话失败。
        参数：`user_id`、`reason` 用于日志。
        返回值：无。
        异常情况：无。provider 异常会作为 `omni.response.cancel.skipped` 观测事件记录。
        """
        if self._conversation is None:
            return
        try:
            with self._operation_lock:
                self._conversation.cancel_response()
        except Exception as exc:  # noqa: BLE001 - provider SDK may raise transport-specific errors
            if self._callbacks:
                self._callbacks.provider_event(
                    {
                        "event": "omni.response.cancel.skipped",
                        "provider": "qwen",
                        "reason": reason,
                        "message": str(exc),
                    }
                )

    def close(self, *, user_id: str, reason: str) -> None:
        """关闭 Omni Realtime 会话。

        主要逻辑：关闭 DashScope conversation，并清理本地引用。
        参数：`user_id`、`reason` 用于日志。
        返回值：无。
        异常情况：异常会转成 callbacks.error。
        """
        if self._conversation is None and not self._provider_slot_acquired:
            return
        try:
            if self._conversation is not None:
                with self._operation_lock:
                    self._conversation.close()
        except Exception as exc:  # noqa: BLE001
            if self._callbacks:
                self._callbacks.error(str(exc), {"event": "omni.session.close.failed", "reason": reason})
        finally:
            self._conversation = None
            self._output_modalities = []
            self._completed_tool_call_ids.clear()
            self._pending_tool_followup_response = None
            self._current_response_audio_emitted = False
            self._release_provider_slot()

    def _acquire_provider_slot(
        self, *, user_id: str, session_id: str, callbacks: RealtimeProviderCallbacks
    ) -> None:
        """占用一个真实 provider 并发连接槽位。

        主要逻辑：在建立 WebSocket 前尝试获取进程内信号量；达到上限时直接失败，
        不继续向供应商发起连接请求。
        参数：`user_id`、`session_id` 用于观测；`callbacks` 负责记录限流事件。
        返回值：无。
        异常情况：达到并发上限时抛出 `RealtimeProviderConcurrencyLimitError`。
        """

        if self._provider_slot_acquired:
            return
        limit = _normalize_realtime_provider_max_concurrent_sessions(self.config.max_concurrent_sessions)
        limiter = _realtime_provider_limiter(
            provider=self.config.provider,
            model=self.config.model,
            websocket_url=self.config.websocket_url,
            max_concurrent_sessions=limit,
        )
        if not limiter.acquire(blocking=False):
            callbacks.provider_event(
                {
                    "event": "omni.provider.concurrency_limited",
                    "provider": "qwen",
                    "model": self.config.model,
                    "user_id": user_id,
                    "session_id": session_id,
                    "max_concurrent_sessions": limit,
                }
            )
            raise RealtimeProviderConcurrencyLimitError(
                f"realtime provider qwen concurrent sessions exceeded limit: {limit}"
            )
        self._provider_limiter = limiter
        self._provider_slot_acquired = True
        callbacks.provider_event(
            {
                "event": "omni.provider.slot.acquired",
                "provider": "qwen",
                "model": self.config.model,
                "user_id": user_id,
                "session_id": session_id,
                "max_concurrent_sessions": limit,
            }
        )

    def _release_provider_slot(self) -> None:
        """释放当前 adapter 持有的 provider 并发连接槽位。"""

        if not self._provider_slot_acquired or self._provider_limiter is None:
            self._provider_slot_acquired = False
            self._provider_limiter = None
            return
        try:
            self._provider_limiter.release()
        finally:
            self._provider_slot_acquired = False
            self._provider_limiter = None

    def _handle_provider_event(self, message: dict[str, Any]) -> None:
        callbacks = self._callbacks
        if callbacks is None:
            return
        event_type = str(message.get("type") or "")
        if event_type == "session.updated":
            self._session_updated.set()
        if event_type == "response.created":
            self._current_response_audio_emitted = False
            callbacks.provider_event(_summarize_omni_event(message))
            return
        if event_type == "response.output_item.added":
            callbacks.provider_event(_summarize_omni_event(message))
            return
        if event_type == "response.audio.delta":
            raw_delta = str(message.get("delta") or "")
            response_id = _omni_response_id(message)
            callbacks.provider_event(
                {
                    "event": "omni.response.audio.delta",
                    "provider": "qwen",
                    "delta_base64_len": len(raw_delta),
                    "response_id": response_id,
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
                        "response_id": response_id,
                    },
                )
                self._current_response_audio_emitted = True
            return
        if event_type == "response.audio.done":
            response_id = _omni_response_id(message)
            callbacks.provider_event({"event": "omni.response.audio.done", "provider": "qwen", "response_id": response_id})
            if self._current_response_audio_emitted:
                callbacks.audio_done({"provider": "qwen", "model": self.config.model, "provider_event": event_type, "response_id": response_id})
            return
        if event_type == "response.done":
            callbacks.provider_event(_summarize_omni_event(message))
            self._create_pending_tool_followup_response()
            self._current_response_audio_emitted = False
            return
        if event_type in {"response.function_call_arguments.delta", "response.tool_call_arguments.delta"}:
            callbacks.provider_event(_summarize_omni_event(message))
            if callbacks.tool_call_delta is not None:
                callbacks.tool_call_delta(
                    {
                        "tool_call_id": str(message.get("call_id") or message.get("item_id") or message.get("id") or ""),
                        "name": message.get("name"),
                        "arguments_delta": message.get("delta") or message.get("arguments_delta") or "",
                    }
                )
            return
        if event_type in {"response.function_call_arguments.done", "response.tool_call.done"}:
            callbacks.provider_event(_summarize_omni_event(message))
            if callbacks.tool_call_done is not None:
                call_id = str(message.get("call_id") or message.get("item_id") or message.get("id") or "")
                if not self._try_mark_tool_call_completed(call_id=call_id, callbacks=callbacks):
                    return
                result = callbacks.tool_call_done(
                    {
                        "tool_call_id": call_id,
                        "name": message.get("name"),
                        "arguments": message.get("arguments") or "",
                    }
                )
                self._submit_tool_result(call_id=call_id, result=result)
                callbacks.provider_event(
                    {
                        "event": "omni.tool_result.ready",
                        "provider": "qwen",
                        "tool_call_id": result.get("tool_call_id"),
                        "tool_name": result.get("name"),
                        "ok": result.get("ok"),
                        "injected": self._conversation is not None,
                    }
                )
            return
        if event_type == "response.output_item.done":
            item = message.get("item") if isinstance(message.get("item"), dict) else {}
            if item.get("type") in {"function_call", "tool_call"} and callbacks.tool_call_done is not None:
                callbacks.provider_event(_summarize_omni_event(message))
                call_id = str(item.get("call_id") or item.get("id") or "")
                if not self._try_mark_tool_call_completed(call_id=call_id, callbacks=callbacks):
                    return
                result = callbacks.tool_call_done(
                    {
                        "tool_call_id": call_id,
                        "name": item.get("name"),
                        "arguments": item.get("arguments") or "",
                    }
                )
                self._submit_tool_result(call_id=call_id, result=result)
                callbacks.provider_event(
                    {
                        "event": "omni.tool_result.ready",
                        "provider": "qwen",
                        "tool_call_id": result.get("tool_call_id"),
                        "tool_name": result.get("name"),
                        "ok": result.get("ok"),
                        "injected": self._conversation is not None,
                    }
                )
                return
        if event_type == "error":
            error = message.get("error") if isinstance(message.get("error"), dict) else {}
            provider_message = str(error.get("message") or message.get("message") or message)
            callbacks.error(
                provider_message,
                {
                    "provider": "qwen",
                    "raw": message,
                    "provider_event_id": message.get("event_id"),
                    "provider_error_code": error.get("code"),
                    "provider_error_type": error.get("type") or message.get("type"),
                    "provider_error_message": error.get("message"),
                },
            )
            return
        callbacks.provider_event(_summarize_omni_event(message))

    def _try_mark_tool_call_completed(self, *, call_id: str, callbacks: RealtimeProviderCallbacks) -> bool:
        """确认同一个 provider 工具调用只被执行和回填一次。

        主要逻辑：Qwen Omni 可能同时发出 `response.function_call_arguments.done`
        和 `response.output_item.done`，两者描述的是同一个 function call。SDK 只应
        执行一次工具并回填一次结果，否则 provider 会因为重复创建 response 而报错。
        参数：`call_id` 为 provider 工具调用 ID，`callbacks` 用于记录重复忽略事件。
        返回值：首次出现返回 True，重复或空 ID 返回 False。
        异常情况：无。
        """

        if not call_id:
            callbacks.provider_event({"event": "omni.tool_call.ignored", "provider": "qwen", "reason": "missing_call_id"})
            return False
        if call_id in self._completed_tool_call_ids:
            callbacks.provider_event(
                {
                    "event": "omni.tool_call.duplicate_ignored",
                    "provider": "qwen",
                    "tool_call_id": call_id,
                }
            )
            return False
        self._completed_tool_call_ids.add(call_id)
        return True

    def _submit_tool_result(self, *, call_id: str, result: dict[str, Any]) -> None:
        """把 ToolResult 回填给当前 Omni Realtime 会话。

        主要逻辑：使用 provider conversation 的 `conversation.item.create`
        写入 `function_call_output`；如果是 `capture_photo` 且结果包含本地图片，则把
        JPEG bytes 追加到同一条 Realtime 会话，随后创建下一段文本和音频响应。
        参数：`call_id` 为 provider 工具调用 ID，`result` 为 SDK ToolGateway 的结果。
        返回值：无。
        异常情况：provider SDK 异常通过 callbacks.error 记录，不向上破坏回调线程。
        """

        if self._conversation is None or not call_id:
            return
        try:
            with self._operation_lock:
                self._conversation.create_item(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps(result, ensure_ascii=False, default=str),
                    }
                )
            image_path = _resolve_capture_photo_tool_image_path(result)
            image_bytes = image_path.read_bytes() if image_path is not None else None
            create_followup_response = True
            response_instructions = _tool_result_followup_instructions(self.config.prompt, result)
            if image_bytes:
                append_video = getattr(self._conversation, "append_video", None)
                if callable(append_video):
                    replay_audio = self._callbacks.replay_audio_for_tool_result(result) if self._callbacks and self._callbacks.replay_audio_for_tool_result else []
                    append_audio = getattr(self._conversation, "append_audio", None)
                    if not replay_audio or not callable(append_audio):
                        if self._callbacks:
                            self._callbacks.provider_event(
                                {
                                    "event": "omni.capture_photo.audio_replay.missing",
                                    "provider": "qwen",
                                    "call_id": call_id,
                                    "tool_name": result.get("name"),
                                    "image_path": str(image_path),
                                    "has_append_audio": callable(append_audio),
                                    "replay_chunk_count": len(replay_audio),
                                    "message": "capture_photo image cannot be replayed because user audio is unavailable",
                                }
                            )
                    else:
                        for audio in replay_audio:
                            if audio:
                                append_audio(base64.b64encode(audio).decode("ascii"))
                        with self._operation_lock:
                            append_video(base64.b64encode(image_bytes).decode("ascii"))
                        commit = getattr(self._conversation, "commit", None)
                        if callable(commit):
                            with self._operation_lock:
                                commit()
                        if self._callbacks:
                            self._callbacks.provider_event(
                                {
                                    "event": "omni.capture_photo.image_appended",
                                    "provider": "qwen",
                                    "tool_call_id": call_id,
                                    "tool_name": result.get("name"),
                                    "image_bytes": len(image_bytes),
                                    "image_path": str(image_path),
                                    "image_sha256": hashlib.sha256(image_bytes).hexdigest(),
                                    "replayed_audio_bytes": sum(len(audio) for audio in replay_audio),
                                    "replayed_audio_chunk_count": len(replay_audio),
                                    "committed": callable(commit),
                                    "response_create": "provider_auto_after_commit",
                                }
                            )
                        response_instructions = _capture_photo_response_instructions(self.config.prompt)
                        create_followup_response = False
                elif self._callbacks:
                    self._callbacks.error(
                        "Realtime provider does not support append_video",
                        {
                            "event": "omni.capture_photo.image_append.unsupported",
                            "call_id": call_id,
                            "tool_name": result.get("name"),
                            "image_path": str(image_path),
                        },
                    )
            elif result.get("name") == "capture_photo" and result.get("ok") and self._callbacks:
                self._callbacks.error(
                    "capture_photo succeeded but image file was not found",
                    {
                        "event": "omni.capture_photo.image_append.missing_image",
                        "call_id": call_id,
                        "tool_name": result.get("name"),
                        "storage_uri": (result.get("data") or {}).get("storage_uri") if isinstance(result.get("data"), dict) else None,
                    },
                )
            if create_followup_response:
                self._pending_tool_followup_response = {
                    "instructions": response_instructions,
                    "output_modalities": self._output_modalities or None,
                    "tool_call_id": call_id,
                    "tool_name": result.get("name"),
                }
                if self._callbacks:
                    self._callbacks.provider_event(
                        {
                            "event": "omni.tool_followup_response.deferred",
                            "provider": "qwen",
                            "tool_call_id": call_id,
                            "tool_name": result.get("name"),
                        }
                    )
        except Exception as exc:  # noqa: BLE001 - provider SDK 异常需要转成可观测事件
            if self._callbacks:
                self._callbacks.error(str(exc), {"event": "omni.tool_result.inject.failed", "call_id": call_id})

    def _create_pending_tool_followup_response(self) -> None:
        """在原始 response 完成后创建工具结果后的 follow-up response。"""

        if self._conversation is None or self._pending_tool_followup_response is None:
            return
        pending = self._pending_tool_followup_response
        self._pending_tool_followup_response = None
        try:
            with self._operation_lock:
                self._conversation.create_response(
                    instructions=pending.get("instructions"),
                    output_modalities=pending.get("output_modalities"),
                )
            if self._callbacks:
                self._callbacks.provider_event(
                    {
                        "event": "omni.tool_followup_response.created",
                        "provider": "qwen",
                        "tool_call_id": pending.get("tool_call_id"),
                        "tool_name": pending.get("tool_name"),
                    }
                )
        except Exception as exc:  # noqa: BLE001 - provider SDK 异常需要转成可观测事件
            if self._callbacks:
                self._callbacks.error(
                    str(exc),
                    {
                        "event": "omni.tool_followup_response.create.failed",
                        "tool_call_id": pending.get("tool_call_id"),
                        "tool_name": pending.get("tool_name"),
                    },
                )


def _resolve_capture_photo_tool_image_path(result: dict[str, Any]) -> Path | None:
    """从 `capture_photo` 工具结果解析本地图片路径。

    主要逻辑：只处理成功的 `capture_photo` 结果，优先从 `data.storage_uri/path/uri` 读取
    本地文件；历史运行产物里的相对路径，避免 server 启动目录不同导致图片追加失败。
    参数：`result` 为 RealtimeToolBridge 生成的工具回填结果。
    返回值：存在的图片路径；不满足条件或文件不存在时返回 None。
    异常情况：无。
    """

    if result.get("name") != "capture_photo" or not result.get("ok"):
        return None
    data = result.get("data")
    if not isinstance(data, dict):
        return None
    raw_path = str(data.get("storage_uri") or data.get("path") or data.get("uri") or "").strip()
    if not raw_path:
        return None
    path = Path(raw_path).expanduser()
    candidates = [path]
    if not path.is_absolute():
        realtime_agent_root = Path(__file__).resolve().parents[3]
        candidates.extend(
            [
                Path.cwd() / path,
                realtime_agent_root / path,
                realtime_agent_root.parent / path,
            ]
        )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _prepare_omni_realtime_image(image: bytes, *, max_bytes: int = OMNI_REALTIME_IMAGE_MAX_BYTES) -> tuple[bytes, dict[str, Any]]:
    """把图片压缩到 Omni Realtime 单个 WebSocket frame 的安全范围内。

    主要逻辑：provider 的 frame 限制按 base64 后的 JSON 帧计算，原始 JPEG 接近
    256KB 时仍可能超限。这里把原始图片压到约 180KB 以下，给 base64 和 JSON 字段
    预留空间。优先降低 JPEG 质量，再逐步缩小尺寸。
    参数：`image` 为原始图片 bytes；`max_bytes` 是压缩后目标大小。
    返回值：压缩后的图片 bytes 以及诊断元数据。
    异常情况：图片无法解码或压缩后仍超限时抛出 `ValueError`。
    """

    if len(image) <= max_bytes:
        return image, {"original_image_bytes": len(image), "image_compressed": False}
    try:
        import cv2  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001 - 缺少图像依赖时给出明确错误
        raise ValueError(f"image is too large for Omni Realtime and image compression dependency is unavailable: {exc}") from exc

    decoded = cv2.imdecode(np.frombuffer(image, dtype=np.uint8), cv2.IMREAD_COLOR)
    if decoded is None:
        raise ValueError(f"image is too large for Omni Realtime and cannot be decoded: bytes={len(image)}")

    best: bytes | None = None
    best_shape: tuple[int, int] | None = None
    height, width = decoded.shape[:2]
    for scale in (1.0, 0.85, 0.7, 0.55, 0.4, 0.3, 0.22):
        target_width = max(1, int(width * scale))
        target_height = max(1, int(height * scale))
        resized = decoded if scale == 1.0 else cv2.resize(decoded, (target_width, target_height), interpolation=cv2.INTER_AREA)
        for quality in (78, 68, 58, 48, 38, 30):
            ok, encoded = cv2.imencode(".jpg", resized, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            if not ok:
                continue
            candidate = encoded.tobytes()
            if best is None or len(candidate) < len(best):
                best = candidate
                best_shape = (target_width, target_height)
            if len(candidate) <= max_bytes:
                return candidate, {
                    "original_image_bytes": len(image),
                    "image_compressed": True,
                    "compressed_image_bytes": len(candidate),
                    "compressed_width": target_width,
                    "compressed_height": target_height,
                    "compressed_jpeg_quality": quality,
                }
    raise ValueError(f"image is too large for Omni Realtime after compression: original={len(image)} best={len(best) if best else 0}")


def _capture_photo_response_instructions(base: str) -> str:
    """构造 capture_photo 后续响应指令。"""

    rule = _registered_prompt_text(
        "capture_photo_followup",
        (
            "刚刚通过 capture_photo 工具提交了一张新的实时照片。"
            "本次回答必须只基于刚提交的这张照片回答用户上一轮视觉问题；"
            "如果它和历史照片或历史描述冲突，以刚提交的新照片为准。"
            "刚才已经完成抓拍，本次不要再次调用 capture_photo。"
            "不要复述工具参数、文件名或调用过程；看不清时直接说明看不清，不能猜测。"
        ),
    )
    return f"{base}\n\n{rule}"


def _tool_result_followup_instructions(base: str, result: dict[str, Any]) -> str:
    """构造工具结果后的 follow-up 响应指令。"""

    if result.get("ok") is not False:
        return base
    error = result.get("error") if isinstance(result.get("error"), dict) else {}
    message = str(result.get("message") or error.get("message") or "工具调用失败").strip()
    name = str(result.get("name") or "工具").strip()
    operation = str((result.get("meta") or {}).get("operation") or "").strip()
    task_rule = ""
    if operation == "task_start" or name == "task_runtime_manager":
        task_rule = "如果这是后台任务或计时器启动失败，必须明确告诉用户任务没有启动、不会按时提醒。"
    rule = _registered_prompt_text(
        "tool_result_failure_followup",
        (
            "刚刚的操作失败了。"
            "失败原因：{message}。"
            "本次回答必须把失败事实直接告知用户，不能声称操作已经执行成功。"
            "不要向用户复述工具名、函数名、参数或调用过程。"
            "{task_rule}"
            "请用简短口语中文说明，并在合适时建议用户重试。"
        ),
    )
    rendered_rule = rule.replace("{message}", message).replace("{task_rule}", task_rule)
    return f"{base}\n\n{rendered_rule}"

def _append_realtime_tool_call_prompt_rule(prompt: str) -> str:
    """追加 Realtime 工具调用语音约束。

    主要逻辑：把“工具请求先调用工具，不要先播报工具准备过程”的规则集中追加到
    provider instructions，避免每个应用配置都重复维护同一段约束。
    参数：`prompt` 为应用配置中的基础提示词。
    返回值：追加约束后的提示词。
    异常情况：无。
    """

    base = prompt.strip()
    if REALTIME_TOOL_CALL_PROMPT_RULE in base:
        return base
    if not base:
        return REALTIME_TOOL_CALL_PROMPT_RULE
    return f"{base}\n\n{REALTIME_TOOL_CALL_PROMPT_RULE}"


class MockRealtimeProviderAdapter:
    """稳定 mock realtime provider。

    主要功能：
    1. 不访问网络，供 `agent.mode=omni` 的单元测试和本地预检使用。
    2. 每次收到输入音频后回调一片原生音频 delta。
    3. `commit_input` 时回调 audio done，模拟 provider 完成一轮响应。

    主要属性：
    1. `config`：provider 配置。
    2. `_callbacks`：OmniRealtimeAgentCore 注入的回调集合。
    3. `appended`：测试可读取的输入 chunk 列表。
    """

    def __init__(self, config: RealtimeProviderConfig | None = None) -> None:
        self.config = config or RealtimeProviderConfig(provider="mock", model="mock-omni")
        self._callbacks: RealtimeProviderCallbacks | None = None
        self.appended: list[StreamChunk] = []
        self.cancelled = False
        self.closed = False

    def open(self, *, user_id: str, session_id: str, callbacks: RealtimeProviderCallbacks) -> None:
        """打开 mock 会话。"""

        self._callbacks = callbacks
        callbacks.provider_event(
            {
                "event": "mock_omni.session.opened",
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

    def append_image(self, image: bytes, *, user_id: str, session_id: str, metadata: dict[str, Any] | None = None) -> None:
        """记录 mock 图片输入。"""

        if self._callbacks is not None:
            self._callbacks.provider_event(
                {
                    "event": "mock_omni.input_image.appended",
                    "provider": "mock",
                    "image_bytes": len(image),
                    **dict(metadata or {}),
                }
            )

    def commit_input(self, *, user_id: str, session_id: str, reason: str) -> None:
        """提交 mock 输入边界并结束音频输出。"""

        if self._callbacks is None:
            return
        self._callbacks.provider_event(
            {
                "event": "mock_omni.input.committed",
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


class OmniOutputAdapter:
    """Realtime 输出适配器。

    主要功能：把 provider 的原生 audio delta 转换为 Output Service 的 native audio
    输入，不经过 VisionRealtimeAgentCore 或 TTS。
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
            intent=OutputItem(user_id=user_id, session_id=session_id, source="omni", priority="normal"),
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
            intent=OutputItem(user_id=user_id, session_id=session_id, source="omni", priority="normal"),
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

        if self.tool_gateway is None:
            return []
        return [
            schema
            for schema in self.tool_gateway.provider_schemas()
            if _provider_tool_schema_name(schema) not in REALTIME_INLINE_VISION_TOOLS
        ]

    def append_tool_call_delta(
        self,
        *,
        tool_call_id: str,
        name: str | None = None,
        arguments_delta: dict | str | None = None,
    ) -> None:
        """聚合 provider 工具调用参数增量。"""

        record = self._pending.setdefault(tool_call_id, {"name": name or "", "arguments": {}, "arguments_text": ""})
        if name:
            record["name"] = name
        if isinstance(arguments_delta, str):
            record["arguments_text"] = str(record.get("arguments_text") or "") + arguments_delta
        else:
            record["arguments"].update(arguments_delta or {})

    def commit_tool_call(
        self,
        *,
        tool_call_id: str,
        user_id: str,
        session_id: str,
        name: str | None = None,
        arguments: dict | str | None = None,
    ) -> dict:
        """提交完整工具调用并返回 provider 回填数据。"""

        if self.tool_gateway is None:
            return {"tool_call_id": tool_call_id, "ok": False, "error": {"message": "tool gateway is not configured"}}
        record = self._pending.pop(tool_call_id, {"name": "", "arguments": {}, "arguments_text": ""})
        if name:
            record["name"] = name
        if arguments is not None:
            if isinstance(arguments, str):
                record["arguments_text"] = arguments
            else:
                record["arguments"].update(arguments)
        name = str(record.get("name") or "")
        resolved_arguments = dict(record.get("arguments") or {})
        arguments_text = str(record.get("arguments_text") or "").strip()
        if arguments_text:
            try:
                decoded = json.loads(arguments_text)
                if isinstance(decoded, dict):
                    resolved_arguments.update(decoded)
                else:
                    resolved_arguments["_raw_arguments"] = decoded
            except json.JSONDecodeError:
                resolved_arguments["_raw_arguments"] = arguments_text
        if self.recorder is not None:
            self.recorder.record_agent_event(
                session_id,
                {"event": "omni.tool_call.committed", "tool_call_id": tool_call_id, "tool_name": name},
            )
        result = self.tool_gateway.call_sync_safe(
            name=name,
            user_id=user_id,
            session_id=session_id,
            input_data=resolved_arguments,
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


class OmniRealtimeAgentCore:
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
        control_service: ControlService | None = None,
        asset_service: AssetService | None = None,
        omni_config: RealtimeProviderConfig | None = None,
        provider_factory: Callable[[RealtimeProviderConfig], RealtimeProviderAdapter] | None = None,
        tool_gateway: ToolGateway | None = None,
        memory_service: Any = None,
        max_context_messages: int = 30,
        **_: Any,
    ) -> None:
        self.output_service = output_service
        self.recorder = recorder
        self.control_service = control_service
        self.asset_service = asset_service
        self.omni_config = omni_config or RealtimeProviderConfig()
        self.memory_service = memory_service
        self.max_context_messages = max(1, int(max_context_messages or 30))
        self.provider_factory = provider_factory or self._default_provider_factory
        self.output_adapter = OmniOutputAdapter(output_service=output_service, recorder=recorder)
        self.tool_bridge = RealtimeToolBridge(tool_gateway=tool_gateway, recorder=recorder)
        self.context_compiler = ContextCompiler()
        self._sessions: dict[str, tuple[str, RealtimeProviderAdapter]] = {}
        self._failed_sessions: set[str] = set()
        self._sessions_with_provider_output: set[str] = set()
        self._event_buffer = AgentEventBuffer()
        self._assistant_text_by_session: dict[str, list[str]] = {}
        self._active_response_sessions: set[str] = set()
        self._response_generation_by_session: dict[str, int] = {}
        self._interrupted_response_generation_by_session: dict[str, int] = {}
        self._response_key_by_session: dict[str, str] = {}
        self._interrupted_response_key_by_session: dict[str, str] = {}
        self._recorded_user_transcripts: set[tuple[str, str]] = set()
        self._tool_call_count_by_session: dict[str, int] = {}
        self._active_user_audio_by_session: dict[str, list[bytes]] = {}
        self._last_user_audio_by_session: dict[str, list[bytes]] = {}
        self._visual_sampler_stop_by_session: dict[str, threading.Event] = {}
        self._visual_sampler_threads_by_session: dict[str, threading.Thread] = {}
        self._visual_sampler_generation_by_session: dict[str, int] = {}
        self._provider_speech_active_by_session: set[str] = set()
        self._audio_since_commit_by_session: set[str] = set()
        self._visual_stream_id_by_session: dict[str, str] = {}
        self._visual_appended_asset_ids_by_session: dict[str, set[str]] = {}
        self._audio_stream_by_session: dict[str, str] = {}
        self._closed_audio_streams_by_session: dict[str, set[str]] = {}
        self._state_by_session: dict[str, str] = {}
        self._user_activity_callback: Callable[[str, str], None] | None = None

    def bind_tool_gateway(self, tool_gateway: ToolGateway) -> None:
        """绑定 Realtime provider 工具桥使用的 ToolGateway。"""

        self.tool_bridge.bind_tool_gateway(tool_gateway)

    def bind_user_activity_callback(self, callback: Callable[[str, str], None]) -> None:
        """绑定有效用户语音活动回调。

        主要逻辑：Omni 链路由 provider 判断用户语音边界，只有 speech_started /
        speech_stopped 这类有效语音事件才刷新连续对话活跃时间，普通静音音频 chunk
        不会刷新空闲计时。
        参数：`callback` 接收 user_id 和 session_id。
        返回值：无。
        异常情况：回调异常会被吞掉并记录为系统事件，避免影响音频主链路。
        """

        self._user_activity_callback = callback

    def open(self, user_id: str, session_id: str) -> None:
        """打开用户 realtime provider 会话。

        主要逻辑：同一 user 已有同 session 时复用；session 变化时先关闭原会话，再打开新会话。
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
        context = self.context_compiler.compile(
            ContextCompileRequest(
                mode="omni",
                provider=self.omni_config.provider,
                model=self.omni_config.model,
                user_id=user_id,
                session_id=session_id,
                base_instructions=self.omni_config.prompt,
                current_input={"type": "input_audio_stream", "stream_type": "sensor.mic"},
                include_tools=True,
                include_realtime_tool_rules=True,
                reason="realtime_session_open",
                memory_service=self.memory_service,
                control_service=self.control_service,
                tool_gateway=self.tool_bridge.tool_gateway,
                max_context_messages=self.max_context_messages,
            )
        )
        tools = list(context.tools)
        session_config = replace(self.omni_config, tools=tools, prompt=context.instructions)
        record_context_events(recorder=self.recorder, session_id=session_id, context=context)
        self.recorder.record_model_request(
            session_id,
            self._build_model_request(
                user_id=user_id,
                session_id=session_id,
                config=session_config,
                tools=tools,
                history_messages=list(context.messages[:-1]) if context.messages else [],
                context=context,
            ),
        )
        provider = self.provider_factory(session_config)
        callbacks = self._callbacks(user_id=user_id, session_id=session_id)
        try:
            provider.open(user_id=user_id, session_id=session_id, callbacks=callbacks)
        except Exception as exc:
            self._record_system_error(
                user_id=user_id,
                session_id=session_id,
                message=str(exc),
                record={"event": "omni.provider.open.failed", "component": "OmniRealtimeAgentCore"},
            )
            raise
        self._sessions[user_id] = (session_id, provider)
        self._record_event(
            "session.opened",
            user_id=user_id,
            session_id=session_id,
            provider=self.omni_config.provider,
            model=self.omni_config.model,
            tool_count=len(tools),
        )
        self._set_turn_state(user_id, session_id, "listening", reason="session_opened")

    def append_audio_event(self, chunk: StreamChunk) -> None:
        """追加 Audio Pipeline 归一后的 sensor.mic chunk。

        主要逻辑：按 chunk 的 user/session 自动打开 provider 会话，然后立即 append 音频；
        不等待 `chunk.final`，turn 判断交给 Omni Realtime。
        参数：`chunk` 为 sensor.mic StreamChunk。
        返回值：无。
        异常情况：非 sensor.mic 或 provider append 失败时抛出异常。
        """
        if chunk.stream_type != "sensor.mic":
            raise ValueError("OmniRealtimeAgentCore only accepts sensor.mic")
        if chunk.session_id in self._failed_sessions:
            return
        self._audio_stream_by_session[chunk.session_id] = chunk.stream_id
        self._closed_audio_streams_by_session.setdefault(chunk.session_id, set()).discard(chunk.stream_id)
        self._cache_replay_audio(chunk)
        self.open(chunk.user_id, chunk.session_id)
        _session_id, provider = self._sessions[chunk.user_id]
        try:
            provider.append_audio(chunk)
        except Exception as exc:
            self._mark_session_failed(
                user_id=chunk.user_id,
                session_id=chunk.session_id,
                message=str(exc),
                record={"event": "omni.provider.append_audio.failed"},
            )
            return
        self.recorder.record_agent_event(
            chunk.session_id,
            {
                "event": "omni.input_audio.appended",
                "provider": self.omni_config.provider,
                "model": self.omni_config.model,
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
        if chunk.payload:
            self._audio_since_commit_by_session.add(chunk.session_id)
            self._mark_realtime_input_activity(
                user_id=chunk.user_id,
                session_id=chunk.session_id,
                reason="input_audio_appended",
            )

    def on_audio_input_opened(self, *, user_id: str, session_id: str, stream_id: str) -> None:
        """记录端侧麦克风输入已打开。

        主要逻辑：麦克风打开只代表端侧进入待听状态，不能提前请求 RGB 上传。
        视觉帧采样由 provider 的 speech_started 事件触发，避免把会话启动时的旧画面
        当成用户当前问题的上下文。
        """

        self._audio_stream_by_session[session_id] = stream_id
        self._closed_audio_streams_by_session.setdefault(session_id, set()).discard(stream_id)

    def on_audio_input_closed(self, *, user_id: str, session_id: str, stream_id: str, reason: str) -> None:
        """通知 Realtime 当前音频输入 stream 已关闭。

        主要逻辑：视觉采样必须和触发它的音频采集一一配对。音频 stream 关闭后，
        立即停止同 session 的视觉采样，避免设备下线或停止录音后继续请求 RGB。
        """

        self._closed_audio_streams_by_session.setdefault(session_id, set()).add(stream_id)
        if self._audio_stream_by_session.get(session_id) == stream_id:
            self._stop_visual_sampler(user_id=user_id, session_id=session_id, reason=f"audio_stream_closed:{reason}")

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
                record={"event": "omni.provider.commit_input.failed"},
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
        if session_id:
            self._mark_current_response_interrupted(user_id=user_id, session_id=session_id, reason=reason)
        if existing:
            existing[1].cancel(user_id=user_id, reason=reason)
        self.output_service.interrupt_user(user_id, session_id=session_id, reason=reason)
        self.recorder.record_agent_event(
            session_id or "realtime-interruptions",
            {"event": "omni.response.cancelled", "user_id": user_id, "reason": reason},
        )
        self._event_buffer.record_event(
            "response.cancelled",
            user_id=user_id,
            session_id=session_id or "",
            payload={"reason": reason},
        )
        if session_id:
            self._set_turn_state(user_id, session_id, "interrupted", reason=reason)

    def close(self, user_id: str, reason: str) -> None:
        """关闭用户 realtime 会话。

        主要逻辑：释放 provider 会话，并取消可能仍在播放的 output stream。
        参数：`user_id` 为用户标识，`reason` 为关闭原因。
        返回值：无。
        异常情况：provider close 异常由 adapter 转成错误事件。
        """
        existing = self._sessions.pop(user_id, None)
        session_id = existing[0] if existing else None
        if session_id:
            self._stop_visual_sampler(user_id=user_id, session_id=session_id, reason=reason)
            self._visual_appended_asset_ids_by_session.pop(session_id, None)
            self._provider_speech_active_by_session.discard(session_id)
            self._audio_since_commit_by_session.discard(session_id)
            self._visual_sampler_generation_by_session.pop(session_id, None)
        if existing:
            existing[1].close(user_id=user_id, reason=reason)
        if session_id:
            self._failed_sessions.discard(session_id)
        self.output_service.interrupt_user(user_id, session_id=session_id, reason=reason)
        if session_id:
            self.recorder.record_agent_event(session_id, {"event": "omni.session.closed", "reason": reason})
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
            audio_delta=lambda audio, fmt, metadata: self._handle_provider_audio_delta(
                user_id=user_id,
                session_id=session_id,
                audio=audio,
                format=fmt,
                metadata=metadata,
            ),
            audio_done=lambda metadata: self._handle_provider_audio_done(
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
            tool_call_delta=lambda record: self._handle_provider_tool_call_delta(record),
            tool_call_done=lambda record: self._handle_provider_tool_call_done(
                user_id=user_id,
                session_id=session_id,
                record=record,
            ),
            replay_audio_for_tool_result=lambda result: self._replay_audio_for_tool_result(
                session_id=session_id,
                result=result,
            ),
        )

    def _cache_replay_audio(self, chunk: StreamChunk) -> None:
        """缓存最近一轮用户原始 PCM，供 capture_photo 后和图片一起重放。"""

        active = self._active_user_audio_by_session.setdefault(chunk.session_id, [])
        if chunk.seq == 0 and active:
            active = []
            self._active_user_audio_by_session[chunk.session_id] = active
        if chunk.payload:
            active.append(bytes(chunk.payload))
        if chunk.final and active:
            self._last_user_audio_by_session[chunk.session_id] = list(active)
            self._active_user_audio_by_session[chunk.session_id] = []

    def _replay_audio_for_tool_result(self, *, session_id: str, result: dict[str, Any]) -> list[bytes]:
        """返回 capture_photo 对应的上一轮用户音频。"""

        if result.get("name") != "capture_photo" or not result.get("ok"):
            return []
        active = self._active_user_audio_by_session.get(session_id) or []
        chunks = active if active else self._last_user_audio_by_session.get(session_id, [])
        self.recorder.record_agent_event(
            session_id,
            {
                "event": "omni.input_audio.replay.prepared",
                "tool_call_id": result.get("tool_call_id"),
                "tool_name": result.get("name"),
                "chunk_count": len(chunks),
                "payload_size": sum(len(chunk) for chunk in chunks),
            },
        )
        return list(chunks)

    def _handle_provider_tool_call_delta(self, record: dict[str, Any]) -> None:
        """处理 provider function call 参数增量。"""

        self.tool_bridge.append_tool_call_delta(
            tool_call_id=str(record.get("tool_call_id") or ""),
            name=record.get("name"),
            arguments_delta=record.get("arguments_delta"),
        )

    def _handle_provider_tool_call_done(
        self,
        *,
        user_id: str,
        session_id: str,
        record: dict[str, Any],
    ) -> dict[str, Any]:
        """提交 provider 工具调用并记录结果回填降级。

        主要逻辑：当前 Qwen adapter 能解析工具调用并执行 ToolGateway，但 provider SDK
        尚无稳定的 tool result injection 封装，因此先把结果写入 runs，并记录明确降级。
        """

        result = self.tool_bridge.commit_tool_call(
            tool_call_id=str(record.get("tool_call_id") or ""),
            user_id=user_id,
            session_id=session_id,
            name=record.get("name"),
            arguments=record.get("arguments"),
        )
        self._set_turn_state(user_id, session_id, "tool_running", reason=str(result.get("name") or record.get("name") or "tool_call"))
        self._tool_call_count_by_session[session_id] = self._tool_call_count_by_session.get(session_id, 0) + 1
        self._append_tool_messages(
            user_id=user_id,
            session_id=session_id,
            tool_call_id=str(result.get("tool_call_id") or record.get("tool_call_id") or ""),
            tool_name=str(result.get("name") or record.get("name") or ""),
            arguments=record.get("arguments"),
            result=result,
        )
        self.recorder.record_agent_event(
            session_id,
            {
                "event": "omni.tool_result.ready",
                "tool_call_id": result.get("tool_call_id"),
                "tool_name": result.get("name"),
                "ok": result.get("ok"),
                "provider_result_injection": "handled_by_provider_adapter",
            },
        )
        self.recorder.record_agent_event(
            session_id,
            {
                "event": "context.source.added",
                "source_id": f"tool_result:{result.get('name') or record.get('name') or ''}",
                "source_kind": "tool",
                "source_name": f"tool_result:{result.get('name') or record.get('name') or ''}",
                "included": True,
                "reason": "realtime_provider_tool_result",
            },
        )
        return result

    def _realtime_tool_schemas(self) -> list[dict[str, Any]]:
        """返回 Omni Realtime 可消费的 function calling schema。

        主要逻辑：ToolGateway 默认输出 OpenAI-compatible `function` 嵌套结构；
        Qwen Omni Realtime 使用 provider 要求的扁平 function schema。
        参数：无。
        返回值：Realtime session.update 可使用的 tools 列表。
        异常情况：未绑定 ToolGateway 时返回空列表。
        """

        if self.tool_bridge.tool_gateway is None:
            return []
        tools: list[dict[str, Any]] = []
        for schema in self.tool_bridge.tool_gateway.provider_schemas():
            if _provider_tool_schema_name(schema) in REALTIME_INLINE_VISION_TOOLS:
                continue
            function = schema.get("function") if isinstance(schema, dict) else None
            if isinstance(function, dict):
                tools.append(
                    {
                        "type": "function",
                        "name": function.get("name"),
                        "description": function.get("description", ""),
                        "parameters": function.get("parameters") or {"type": "object", "properties": {}},
                    }
                )
            elif isinstance(schema, dict):
                tools.append(schema)
        return tools

    def _load_runtime_messages(self, *, user_id: str, session_id: str) -> list[dict[str, Any]]:
        """读取 Realtime 会话启动时可注入的历史对话。

        主要逻辑：读取 active user/assistant 文本后写入等价 `model_request.messages`
        视图；system 只承载基础提示词、长期记忆和已压缩摘要，未压缩历史必须按
        原角色平铺在 messages 中，避免混入 system prompt。
        参数：`user_id/session_id` 定位同一用户同一设备的 `messages.jsonl`。
        返回值：模型可读的历史消息列表。
        异常情况：读取失败时返回空列表。
        """

        if self.control_service is None:
            return []
        try:
            records = self.control_service.load_messages(
                user_id=user_id,
                session_id=session_id,
                limit=self.max_context_messages,
            )
        except Exception:
            return []
        messages = [_normalize_history_message(record) for record in records]
        return [message for message in messages if message is not None]

    def _load_message_summary_fragment(self, *, user_id: str, session_id: str) -> str:
        """读取更早历史对话摘要提示词。

        主要逻辑：只读取已压缩历史摘要；未压缩 active 历史由 `messages` 平铺表达。
        参数：`user_id/session_id` 定位用户设备。
        返回值：可追加到 instructions 的提示词片段。
        异常情况：无；底层异常会被吞掉并返回空字符串。
        """

        if self.control_service is None:
            return ""
        try:
            return self.control_service.load_message_summary_fragment(user_id=user_id, session_id=session_id)
        except Exception:
            return ""

    def _build_model_request(
        self,
        *,
        user_id: str,
        session_id: str,
        config: RealtimeProviderConfig,
        tools: list[dict[str, Any]],
        history_messages: list[dict[str, Any]] | None = None,
        context: Any = None,
    ) -> dict[str, Any]:
        """构造 Omni Realtime 模型请求快照。

        主要逻辑：Realtime 底层不是 Chat Completions `messages` 参数，但开发者排障
        需要看到等价的 system/user/assistant 视图。system 只包含基础提示词、长期记忆
        和已压缩摘要；未压缩 active 历史按原 role 平铺在 messages 中，最后追加当前
        音频输入占位。
        参数：`user_id/session_id/config/tools` 描述当前会话；`history_messages`
        是未压缩 active 历史。
        返回值：可写入 `model-request.json` 的结构。
        异常情况：无。
        """

        history = list(history_messages or [])
        messages: list[dict[str, Any]]
        if context is not None and getattr(context, "messages", None):
            messages = [{"role": "system", "content": config.prompt}, *list(context.messages)]
        else:
            messages = [
                {"role": "system", "content": config.prompt},
                *history,
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio_stream",
                            "stream_type": "sensor.mic",
                            "note": "Realtime 底层持续发送 PCM 音频；这里是等价请求视图。",
                        }
                    ],
                },
            ]
        record = {
            "provider": config.provider,
            "model": config.model,
            "runner": "agent_core_omni_audio",
            "prompt": config.prompt,
            "messages": messages,
            "active_history_message_count": len(history),
            "active_history_injected_to": "messages" if history else "",
            "tools": tools,
            "tool_count": len(tools),
            "user_id": user_id,
            "session_id": session_id,
        }
        if context is not None:
            record.update(
                {
                    "prompts": context.prompt_records(),
                    "context_sources": context.source_records(),
                    "warnings": context.warnings,
                    "truncations": context.truncations,
                    "notifications": context.notifications,
                    "context_metadata": context.metadata,
                }
            )
        return record

    def _build_prompt(self, *, user_id: str) -> str:
        """构造当前 realtime 会话提示词。

        主要逻辑：在静态 Omni 指令后追加长期记忆片段，让模型直接获得当前用户的已保存信息。
        参数：`user_id` 为当前用户编号。
        返回值：发送给 Realtime provider 的提示词。
        异常情况：memory 未启用或读取失败时只返回基础指令。
        """

        base = _append_realtime_tool_call_prompt_rule(self.omni_config.prompt)
        memory = self.memory_service
        if memory is None or not getattr(memory, "enabled", False):
            return base
        try:
            fragment = memory.build_prompt_fragment(user_id=user_id)
        except Exception:
            return base
        if not fragment:
            return base
        return f"{base}\n\n{fragment}"

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
        self._stop_visual_sampler(user_id=user_id, session_id=session_id, reason="provider_failed")
        self._provider_speech_active_by_session.discard(session_id)
        self._audio_since_commit_by_session.discard(session_id)
        existing = self._sessions.pop(user_id, None)
        if existing:
            try:
                existing[1].close(user_id=user_id, reason="provider_failed")
            except Exception:
                pass
        if first_failure:
            self._set_turn_state(user_id, session_id, "failed", reason=str(record.get("event") or "provider_failed"))
            self._record_system_error(
                user_id=user_id,
                session_id=session_id,
                message=message,
                record={"component": "RealtimeProviderAdapter", **record},
            )
            self.recorder.record_agent_event(
                session_id,
                {
                    "event": "omni.session.failed",
                    "provider": self.omni_config.provider,
                    "model": self.omni_config.model,
                    "message": message,
                    "provider_event_id": record.get("provider_event_id"),
                    "provider_error_code": record.get("provider_error_code"),
                    "provider_error_type": record.get("provider_error_type"),
                    "provider_error_message": record.get("provider_error_message"),
                },
            )

    def _record_provider_event(self, *, user_id: str, session_id: str, record: dict[str, Any]) -> None:
        """记录 provider 事件到 runs 和统一事件缓存。"""

        self.recorder.record_agent_event(session_id, record)
        self._track_provider_response_event(session_id=session_id, record=record)
        self._track_provider_input_buffer_state(session_id=session_id, record=record)
        self._map_provider_turn_state(user_id=user_id, session_id=session_id, record=record)
        self._handle_provider_speech_started_interrupt(user_id=user_id, session_id=session_id, record=record)
        self._handle_provider_speech_stopped(user_id=user_id, session_id=session_id, record=record)
        self._handle_visual_sampler_provider_event(user_id=user_id, session_id=session_id, record=record)
        self._capture_provider_message(user_id=user_id, session_id=session_id, record=record)
        self._event_buffer.record_event(
            str(record.get("event") or "provider.event"),
            user_id=user_id,
            session_id=session_id,
            payload=dict(record),
        )

    def _track_provider_input_buffer_state(self, *, session_id: str, record: dict[str, Any]) -> None:
        """跟踪 provider 当前输入 buffer 是否仍能接收图片。

        主要逻辑：Qwen Omni 要求同一输入 buffer 中先有音频，图片只能作为该轮
        音频的补充。provider 自动提交后，下一轮 buffer 重新变为空；此时迟到的
        图片必须丢弃，不能先于下一轮音频追加。
        """

        event = str(record.get("event") or "")
        if event == "omni.input_audio_buffer.speech_started":
            self._provider_speech_active_by_session.add(session_id)
            self._audio_since_commit_by_session.add(session_id)
        elif event in {"omni.input_audio_buffer.speech_stopped", "omni.input_audio_buffer.committed", "omni.input.committed"}:
            self._provider_speech_active_by_session.discard(session_id)
            if event != "omni.input_audio_buffer.speech_stopped":
                self._audio_since_commit_by_session.discard(session_id)
        elif event == "omni.response.done":
            self._provider_speech_active_by_session.discard(session_id)

    def _map_provider_turn_state(self, *, user_id: str, session_id: str, record: dict[str, Any]) -> None:
        """把 Omni provider 原始事件映射到统一 turn 状态事件。"""

        event = str(record.get("event") or "")
        state = ""
        if event == "omni.input_audio_buffer.speech_started":
            state = "user_speaking"
        elif event in {"omni.input_audio_buffer.speech_stopped", "omni.input.committed"}:
            state = "thinking"
        elif event == "omni.response.done":
            status = str(record.get("status") or "").lower()
            if self._is_current_response_interrupted(session_id=session_id):
                self.recorder.record_agent_event(
                    session_id,
                    {
                        "event": "omni.response.done_ignored_after_interrupt",
                        "reason": "interrupted_response",
                    },
                )
                return
            if status == "cancelled":
                state = "interrupted"
            else:
                state = "completed"
        if state:
            self._set_turn_state(user_id, session_id, state, reason=event, provider_event=event)

    def _mark_realtime_input_activity(self, *, user_id: str, session_id: str, reason: str) -> None:
        """记录连续麦克风输入，但不覆盖正在生成或播放的 turn 状态。

        主要逻辑：实时链路中麦克风会持续上行，input chunk 只能证明连接仍在工作，
        不能说明用户开始或停止说话；真实 turn boundary 必须来自 provider speech 事件。
        参数：`user_id/session_id` 定位会话，`reason` 写入诊断事件。
        返回值：无。
        异常情况：无。
        """

        current_state = self._state_by_session.get(session_id, "")
        if current_state in {"", "completed", "interrupted", "failed"}:
            self._set_turn_state(user_id, session_id, "listening", reason=reason)

    def _handle_provider_audio_delta(
        self,
        *,
        user_id: str,
        session_id: str,
        audio: bytes,
        format: StreamFormat,
        metadata: dict[str, Any],
    ) -> None:
        """处理 provider 下行音频增量，并丢弃已打断 generation 的迟到音频。

        主要逻辑：用户打断后，旧 provider 可能仍继续吐音频；这些音频不能再写入
        OutputService，否则端侧会继续听到旧回答。
        参数：`audio/format/metadata` 为 provider 音频回调内容。
        返回值：无。
        异常情况：OutputService 写入异常向上抛出。
        """

        response_key = self._response_key_from_record(metadata)
        if self._is_response_inactive(session_id=session_id, response_key=response_key):
            self.recorder.record_agent_event(
                session_id,
                {
                    "event": "omni.response.audio_delta_ignored_after_interrupt",
                    "reason": "interrupted_response",
                    "payload_size": len(audio),
                    "response_key": response_key,
                },
            )
            return
        self.output_adapter.emit_audio_delta(
            user_id=user_id,
            session_id=session_id,
            audio=audio,
            format=format,
            metadata=metadata,
        )
        self._set_turn_state(user_id, session_id, "speaking", reason="audio_delta")
        self._sessions_with_provider_output.add(session_id)

    def _handle_provider_audio_done(self, *, user_id: str, session_id: str, metadata: dict[str, Any]) -> None:
        """处理 provider 下行音频完成，并忽略已打断 generation 的迟到完成回调。

        主要逻辑：打断是正常控制流，旧 generation 的 audio_done 不能重新 finish
        已取消的 output stream，也不能把 turn 状态改成 completed。
        参数：`metadata` 为 provider 完成事件摘要。
        返回值：无。
        异常情况：OutputService 写入异常向上抛出。
        """

        response_key = self._response_key_from_record(metadata)
        if self._is_response_inactive(session_id=session_id, response_key=response_key):
            self.recorder.record_agent_event(
                session_id,
                {
                    "event": "omni.response.audio_done_ignored_after_interrupt",
                    "reason": "interrupted_response",
                    "provider": metadata.get("provider"),
                    "model": metadata.get("model"),
                    "response_key": response_key,
                },
            )
            return
        self.output_adapter.emit_audio_done(user_id=user_id, session_id=session_id, metadata=metadata)
        self._set_turn_state(user_id, session_id, "completed", reason="audio_done")

    def _track_provider_response_event(self, *, session_id: str, record: dict[str, Any]) -> None:
        """跟踪 provider response 轮次，便于区分被打断的旧响应。

        主要逻辑：Omni provider 可能在取消后仍然吐出旧 response 的 done/transcript。
        这里用 session 内递增序号标记当前 response，打断时只屏蔽同一轮次的旧输出。
        参数：`session_id` 为会话标识，`record` 为 provider 事件。
        返回值：无。
        异常情况：无。
        """

        event = str(record.get("event") or "")
        if event == "omni.response.done":
            self._active_response_sessions.discard(session_id)
            return
        if event != "omni.response.created":
            return
        self._active_response_sessions.add(session_id)
        self._response_generation_by_session[session_id] = self._response_generation_by_session.get(session_id, 0) + 1
        generation = self._response_generation_by_session[session_id]
        self._response_key_by_session[session_id] = self._response_key_from_record(record) or f"generation:{generation}"
        self._assistant_text_by_session.pop(session_id, None)

    def _mark_current_response_interrupted(self, *, user_id: str, session_id: str, reason: str) -> None:
        """标记当前 provider response 已被用户打断。

        主要逻辑：打断时封存已累计但尚未落库的助手文本，并记录当前 response 轮次，
        后续同轮次的 transcript done 不再追加到 messages。若 OutputService 能提供播放
        进度估算，则把 `<用户打断>` 插入到已播放和已生成未播放文本之间；Omni 原生
        audio 暂无 provider 字幕游标时保守放在已生成文本末尾。
        参数：`session_id` 为会话标识，`reason` 为打断原因。
        返回值：无。
        异常情况：无。
        """

        generation = self._response_generation_by_session.get(session_id, 0)
        self._interrupted_response_generation_by_session[session_id] = generation
        self._interrupted_response_key_by_session[session_id] = self._response_key_by_session.get(session_id, f"generation:{generation}")
        partial = "".join(self._assistant_text_by_session.get(session_id, [])).strip()
        if partial and self.control_service is not None:
            played_text = partial
            unheard_text = ""
            split_source = "omni_generated_tail"
            estimate = getattr(self.output_service, "estimate_played_text_prefix", None)
            if callable(estimate):
                try:
                    estimated_played = estimate(user_id=user_id, session_id=session_id)
                except Exception as exc:  # noqa: BLE001
                    estimated_played = None
                    self.recorder.record_agent_event(
                        session_id,
                        {
                            "event": "omni.response.interrupted_played_text_estimate_failed",
                            "reason": reason,
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                    )
                if isinstance(estimated_played, str) and len(estimated_played) < len(partial):
                    played_text = estimated_played
                    unheard_text = partial[len(estimated_played) :]
                    split_source = "output_service_estimate"
            content = f"{played_text}<用户打断>{unheard_text}"
            self.control_service.append_message(
                user_id,
                {
                    "session_id": session_id,
                    "role": "assistant",
                    "content": content,
                    "event": "assistant_text.interrupted",
                    "source": "omni_realtime",
                },
            )
        else:
            played_text = ""
            unheard_text = ""
            split_source = "empty_partial"
        self._assistant_text_by_session.pop(session_id, None)
        self.recorder.record_agent_event(
            session_id,
            {
                "event": "omni.response.marked_interrupted",
                "reason": reason,
                "response_generation": generation,
                "partial_chars": len(partial),
                "played_chars": len(played_text),
                "unheard_chars": len(unheard_text),
                "split_source": split_source,
            },
        )

    def _is_current_response_interrupted(self, *, session_id: str) -> bool:
        """判断当前 provider response 是否已被打断。"""

        generation = self._response_generation_by_session.get(session_id, 0)
        return self._interrupted_response_generation_by_session.get(session_id) == generation

    def _response_key_from_record(self, record: dict[str, Any] | None) -> str:
        """从 provider record 或 metadata 中提取稳定 response 标识。"""

        if not isinstance(record, dict):
            return ""
        response = record.get("response") if isinstance(record.get("response"), dict) else {}
        item = record.get("item") if isinstance(record.get("item"), dict) else {}
        value = (
            record.get("response_id")
            or record.get("responseId")
            or record.get("id")
            or response.get("id")
            or item.get("response_id")
        )
        return str(value or "")

    def _is_response_inactive(self, *, session_id: str, response_key: str = "") -> bool:
        """判断 provider 回调是否属于已打断或过期 response。"""

        if response_key:
            current_key = self._response_key_by_session.get(session_id, "")
            if current_key and response_key != current_key:
                return True
            return self._interrupted_response_key_by_session.get(session_id) == response_key
        return self._is_current_response_interrupted(session_id=session_id)

    def _handle_provider_speech_started_interrupt(self, *, user_id: str, session_id: str, record: dict[str, Any]) -> None:
        """根据 Omni provider 的 speech_started 事件取消旧输出。

        主要逻辑：连续对话中，用户是否开始说话由 Omni provider 判断；服务端收到
        `speech_started` 后先下发统一 `audio.speech.started` 控制事件，再取消 provider
        当前响应，并通过 Output Service 下发 `stream.output.cancel.requested`。
        """

        event = str(record.get("event") or "")
        if event != "omni.input_audio_buffer.speech_started":
            return
        reason = "provider_speech_started"
        stream_id = self._audio_stream_by_session.get(session_id, "")
        self._notify_user_activity(user_id=user_id, session_id=session_id, reason=reason)
        if getattr(self, "_pipeline_event_control_enabled", False):
            return
        self._publish_provider_speech_event(
            event_name="audio.speech.started",
            user_id=user_id,
            session_id=session_id,
            stream_id=stream_id,
            reason=reason,
            record=record,
        )
        active_stream_id = self.output_service.active_output_stream_id(user_id, session_id)
        response_active = session_id in self._active_response_sessions
        if active_stream_id is None and not response_active:
            self.recorder.record_agent_event(
                session_id,
                {
                    "event": "omni.provider_speech_started.no_active_response",
                    "reason": reason,
                },
            )
            self._event_buffer.record_event(
                "provider_speech_started.no_active_response",
                user_id=user_id,
                session_id=session_id,
                payload={"reason": reason},
            )
            return
        self._mark_current_response_interrupted(user_id=user_id, session_id=session_id, reason=reason)
        existing = self._sessions.get(user_id)
        if existing:
            existing[1].cancel(user_id=user_id, reason=reason)
        decision = self.output_service.interrupt_user(user_id, session_id=session_id, reason=reason)
        if active_stream_id is None:
            self.recorder.record_agent_event(
                session_id,
                {
                    "event": "omni.provider_speech_started.no_active_output",
                    "reason": reason,
                    "playback_action": decision.action,
                    "playback_reason": decision.reason,
                },
            )
        self.recorder.record_agent_event(
            session_id,
            {
                "event": "omni.provider_speech_started.interrupt",
                "reason": reason,
                "interrupted_stream_id": decision.interrupted_stream_id,
                "playback_action": decision.action,
                "playback_reason": decision.reason,
            },
        )
        self._event_buffer.record_event(
            "provider_speech_started.interrupt",
            user_id=user_id,
            session_id=session_id,
            payload={
                "reason": reason,
                "interrupted_stream_id": decision.interrupted_stream_id,
                "playback_action": decision.action,
                "playback_reason": decision.reason,
            },
        )

    def _handle_provider_speech_stopped(self, *, user_id: str, session_id: str, record: dict[str, Any]) -> None:
        """把 Omni provider 的 speech_stopped 事件发布成统一端侧控制事件。

        主要逻辑：端侧只理解标准 `audio.speech.stopped`，不应该感知 Omni 原始事件名。
        参数：`record` 为 provider 事件摘要。
        返回值：无。
        异常情况：无。
        """

        event = str(record.get("event") or "")
        if event != "omni.input_audio_buffer.speech_stopped":
            return
        reason = "provider_speech_stopped"
        self._notify_user_activity(user_id=user_id, session_id=session_id, reason=reason)
        if getattr(self, "_pipeline_event_control_enabled", False):
            return
        self._publish_provider_speech_event(
            event_name="audio.speech.stopped",
            user_id=user_id,
            session_id=session_id,
            stream_id=self._audio_stream_by_session.get(session_id, ""),
            reason=reason,
            record=record,
        )

    def _publish_provider_speech_event(
        self,
        *,
        event_name: str,
        user_id: str,
        session_id: str,
        stream_id: str,
        reason: str,
        record: dict[str, Any],
    ) -> None:
        """向端侧发布 Omni provider 归一后的用户语音边界事件。

        主要逻辑：浏览器端需要根据 `audio.speech.started` 暂停播放器写入并清空
        本地播放队列；该语义不能依赖服务器侧 output stream 是否仍处于 active。
        参数：`event_name` 为标准控制事件名；`record` 用于携带诊断信息。
        返回值：无。
        异常情况：ControlService 发布异常会写入 system event，避免打断流程中断。
        """

        if self.control_service is None:
            return
        diagnostics = {
            "provider_event": record.get("event"),
            "provider": record.get("provider"),
            "model": record.get("model"),
        }
        try:
            self.control_service.publish(
                Event(
                    event_name=event_name,
                    user_id=user_id,
                    producer_id=SERVER_PRODUCER_ID,
                    session_id=session_id,
                    payload={
                        "stream_id": stream_id,
                        "reason": reason,
                        "diagnostics": diagnostics,
                    },
                )
            )
        except Exception as exc:  # noqa: BLE001
            self.recorder.record_system_event(
                {
                    "event": "system.error.raised",
                    "component": "OmniRealtimeAgentCore",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "reason": reason,
                    "user_id": user_id,
                    "session_id": session_id,
                }
            )

    def _notify_user_activity(self, *, user_id: str, session_id: str, reason: str) -> None:
        """通知外层连续对话有真实用户语音活动。"""

        if self._user_activity_callback is None:
            return
        try:
            self._user_activity_callback(user_id, session_id)
        except Exception as exc:  # noqa: BLE001
            self.recorder.record_system_event(
                {
                    "event": "system.error.raised",
                    "component": "OmniRealtimeAgentCore",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "reason": reason,
                    "user_id": user_id,
                    "session_id": session_id,
                }
            )

    def _handle_visual_sampler_provider_event(self, *, user_id: str, session_id: str, record: dict[str, Any]) -> None:
        """根据 provider VAD 事件管理视觉采样生命周期。

        主要逻辑：端侧摄像头预览可以常开，但向模型上传的 RGB 帧只能跟随真实用户
        语音 turn。provider 确认 speech_started 后开始按需请求单帧，speech_stopped 后
        停止后续请求，避免会话空闲阶段继续采集或复用旧图。
        """

        event = str(record.get("event") or "")
        if event == "omni.input_audio_buffer.speech_started":
            self._start_visual_sampler(user_id=user_id, session_id=session_id)
        elif event == "omni.input_audio_buffer.speech_stopped":
            self._stop_visual_sampler(user_id=user_id, session_id=session_id, reason="provider_speech_stopped")

    def _start_visual_sampler(self, *, user_id: str, session_id: str) -> None:
        """启动当前用户语音 turn 的实时视觉帧消费线程。"""

        if not bool(self.omni_config.realtime_video_enabled):
            return
        interval = float(self.omni_config.visual_frame_interval_seconds or 0)
        if interval <= 0 or self.asset_service is None:
            return
        existing = self._visual_sampler_threads_by_session.get(session_id)
        if existing and existing.is_alive():
            return
        stop_event = threading.Event()
        generation = self._visual_sampler_generation_by_session.get(session_id, 0) + 1
        self._visual_sampler_generation_by_session[session_id] = generation
        self._visual_sampler_stop_by_session[session_id] = stop_event
        thread = threading.Thread(
            target=self._visual_sampler_loop,
            kwargs={
                "user_id": user_id,
                "session_id": session_id,
                "stop_event": stop_event,
                "interval": interval,
                "generation": generation,
            },
            name=f"realtime-visual-{session_id}",
            daemon=True,
        )
        self._visual_sampler_threads_by_session[session_id] = thread
        self.recorder.record_agent_event(
            session_id,
            {
                "event": "omni.visual_sampler.started",
                "provider": self.omni_config.provider,
                "interval_seconds": interval,
                "generation": generation,
            },
        )
        thread.start()

    def _request_visual_stream_open(self, *, user_id: str, session_id: str) -> None:
        """按标准协议请求端侧打开 continuous RGB 输入链路。

        说明：该方法保留给真正 continuous RGB 设备使用。当前 Omni 主链路默认通过
        AssetService 按 speech turn 请求单帧，避免会话启动后端侧只上传首帧造成旧图。
        """

        if self.control_service is None:
            return
        stream_id = self._visual_stream_id_by_session.get(session_id)
        if stream_id is None:
            stream_id = new_id("stream_rgb")
            self._visual_stream_id_by_session[session_id] = stream_id
        frequency_hz = 1.0 / max(0.001, float(self.omni_config.visual_frame_interval_seconds or 1.0))
        event = Event(
            event_name="stream.control.open.requested",
            user_id=user_id,
            producer_id=SERVER_PRODUCER_ID,
            session_id=session_id,
            stream_id=stream_id,
            stream_type="sensor.rgb",
            payload={
                "stream_type": "sensor.rgb",
                "mode": "continuous",
                "format": "jpeg",
                "frequency_hz": frequency_hz,
                "ttl_seconds": self.omni_config.visual_frame_ttl_seconds,
                "capture_reason": "realtime_video",
                "direction": self.omni_config.visual_direction,
                "reason": "audio_session_opened",
            },
        )
        matched = [
            device
            for device in self.control_service.resolve_matching_devices(event, selection="all")
            if device.device_id == session_id
        ]
        publish_result = self.control_service._push_event_to_device_ids(
            event,
            tuple(device.device_id for device in matched),
        )
        self.recorder.record_agent_event(
            session_id,
            {
                "event": "omni.visual_stream.open.requested",
                "provider": self.omni_config.provider,
                "stream_id": stream_id,
                "mode": "continuous",
                "frequency_hz": frequency_hz,
                "matched_count": publish_result.matched_count,
                "delivered_count": publish_result.delivered_count,
                "matched_device_ids": list(publish_result.matched_device_ids),
            },
        )

    def _stop_visual_sampler(self, *, user_id: str, session_id: str, reason: str) -> None:
        """停止当前 turn 的视觉帧采样线程。"""

        stop_event = self._visual_sampler_stop_by_session.pop(session_id, None)
        thread = self._visual_sampler_threads_by_session.pop(session_id, None)
        if stop_event is None and thread is None:
            return
        if stop_event is not None:
            stop_event.set()
        self._visual_sampler_generation_by_session[session_id] = (
            self._visual_sampler_generation_by_session.get(session_id, 0) + 1
        )
        self.recorder.record_agent_event(
            session_id,
            {
                "event": "omni.visual_sampler.stopped",
                "provider": self.omni_config.provider,
                "reason": reason,
                "generation": self._visual_sampler_generation_by_session.get(session_id),
            },
        )
        self._request_visual_stream_close(user_id=user_id, session_id=session_id, reason=reason)

    def _request_visual_stream_close(self, *, user_id: str, session_id: str, reason: str) -> None:
        """通知端侧关闭本轮 Realtime 视觉采集。

        主要逻辑：视觉帧是按需追加的，不应该让端侧摄像头跨 turn 常开。采样停止时，
        向支持 `sensor.rgb` 的设备广播 `stream.control.close.requested`，浏览器参考端
        收到后会停止 `MediaStream`。
        """

        if self.control_service is None:
            return
        stream_id = self._visual_stream_id_by_session.pop(session_id, None)
        if stream_id is None:
            return
        event = Event(
            event_name="stream.control.close.requested",
            user_id=user_id,
            producer_id=SERVER_PRODUCER_ID,
            session_id=session_id,
            stream_id=stream_id,
            stream_type="sensor.rgb",
            payload={"stream_type": "sensor.rgb", "mode": "continuous", "reason": f"realtime_visual_sampler_{reason}"},
        )
        matched = [
            device
            for device in self.control_service.resolve_matching_devices(event, selection="all")
            if device.device_id == session_id
        ]
        publish_result = self.control_service._push_event_to_device_ids(
            event,
            tuple(device.device_id for device in matched),
        )
        self.recorder.record_agent_event(
            session_id,
            {
                "event": "omni.visual_stream.close.requested",
                "provider": self.omni_config.provider,
                "reason": reason,
                "matched_count": publish_result.matched_count,
                "delivered_count": publish_result.delivered_count,
                "matched_device_ids": list(publish_result.matched_device_ids),
            },
        )

    def _visual_sampler_loop(
        self,
        *,
        user_id: str,
        session_id: str,
        stop_event: threading.Event,
        interval: float,
        generation: int,
    ) -> None:
        """按固定间隔请求并追加当前视觉帧。

        主要逻辑：每次循环都通过 AssetService 请求 `sensor.rgb` 单帧。端侧如果摄像头
        已打开，应直接抓取当前帧；如果未打开，会在处理 `stream.control.open.requested`
        时打开摄像头并上传。请求成功后把本地 JPEG bytes 追加到 provider。
        """

        frame_index = 0
        timeout = float(self.omni_config.visual_frame_timeout_seconds or 1.5)
        while not stop_event.is_set():
            started_at = time.monotonic()
            try:
                if not self._can_append_visual_frame(session_id=session_id, generation=generation):
                    self._record_visual_frame_discarded(
                        session_id=session_id,
                        frame_index=frame_index,
                        reason="visual_turn_inactive",
                        generation=generation,
                    )
                    return
                if not self._has_paired_visual_capture_device(user_id=user_id, session_id=session_id):
                    self.recorder.record_agent_event(
                        session_id,
                        {
                            "event": "omni.visual_sampler.paired_stream_unavailable",
                            "provider": self.omni_config.provider,
                            "frame_index": frame_index,
                            "audio_stream_id": self._audio_stream_by_session.get(session_id),
                        },
                    )
                    self._stop_visual_sampler(user_id=user_id, session_id=session_id, reason="paired_stream_unavailable")
                    return
                self._request_and_append_visual_frame(
                    user_id=user_id,
                    session_id=session_id,
                    frame_index=frame_index,
                    timeout_seconds=timeout,
                    generation=generation,
                )
            except Exception as exc:  # noqa: BLE001 - 后台采样异常只能记录，不能打断音频主链路
                self.recorder.record_agent_event(
                    session_id,
                    {
                        "event": "omni.visual_frame.failed",
                        "provider": self.omni_config.provider,
                        "frame_index": frame_index,
                        "message": str(exc),
                    },
                )
            frame_index += 1
            elapsed = time.monotonic() - started_at
            stop_event.wait(max(0.0, interval - elapsed))

    def _can_append_visual_frame(self, *, session_id: str, generation: int) -> bool:
        """判断图片是否仍属于当前 provider 语音输入 buffer。

        主要逻辑：采样线程可能正在等待端侧上传图片；等待期间 provider 可能已经
        speech_stopped 并自动提交 buffer。只有采样代际仍匹配、provider 仍处于当前
        用户语音段，并且本轮 buffer 已经追加过音频时，才允许追加图片。
        """

        return (
            self._visual_sampler_generation_by_session.get(session_id) == generation
            and session_id in self._provider_speech_active_by_session
            and session_id in self._audio_since_commit_by_session
        )

    def _record_visual_frame_discarded(
        self,
        *,
        session_id: str,
        frame_index: int,
        reason: str,
        generation: int,
        asset_id: str | None = None,
    ) -> None:
        """记录被丢弃的视觉帧，便于区分协议保护和端侧丢图。"""

        self.recorder.record_agent_event(
            session_id,
            {
                "event": "omni.visual_frame.discarded",
                "provider": self.omni_config.provider,
                "frame_index": frame_index,
                "reason": reason,
                "generation": generation,
                "current_generation": self._visual_sampler_generation_by_session.get(session_id),
                "speech_active": session_id in self._provider_speech_active_by_session,
                "audio_since_commit": session_id in self._audio_since_commit_by_session,
                **({"asset_id": asset_id} if asset_id else {}),
            },
        )

    def _has_paired_visual_capture_device(self, *, user_id: str, session_id: str) -> bool:
        """检查当前音频设备是否仍在线且支持 RGB 采集。

        主要逻辑：视觉采样必须和同一个设备的音频采集配对。这里先检查音频 stream
        是否已关闭，再检查同一 session/device 是否仍是在线 RGB 路由目标。
        """

        audio_stream_id = self._audio_stream_by_session.get(session_id)
        if audio_stream_id and audio_stream_id in self._closed_audio_streams_by_session.get(session_id, set()):
            return False
        if self.control_service is None:
            return True
        event = Event(
            event_name="stream.control.open.requested",
            user_id=user_id,
            producer_id=SERVER_PRODUCER_ID,
            session_id=session_id,
            stream_type="sensor.rgb",
            payload={"stream_type": "sensor.rgb", "mode": "single", "reason": "realtime_visual_sampler_probe"},
        )
        return any(
            device.device_id == session_id
            for device in self.control_service.resolve_matching_devices(event, selection="all")
        )

    def _request_and_append_visual_frame(
        self,
        *,
        user_id: str,
        session_id: str,
        frame_index: int,
        timeout_seconds: float,
        generation: int,
    ) -> None:
        """请求一张当前 RGB 帧并追加到当前 Realtime provider。"""

        if self.asset_service is None:
            return
        existing = self._sessions.get(user_id)
        if not existing or existing[0] != session_id:
            return
        asset = self.asset_service.request_asset(
            user_id=user_id,
            stream_type="sensor.rgb",
            freshness_seconds=0.0,
            params={
                "format": "jpeg",
                "frequency_hz": 1,
                "sample_count": 1,
                "duration_seconds": 0,
                "ttl_seconds": self.omni_config.visual_frame_ttl_seconds,
                "capture_reason": "realtime_video",
                "direction": self.omni_config.visual_direction,
            },
            session_id=session_id,
            timeout_seconds=max(0.05, timeout_seconds),
            device_ids=(session_id,),
        )
        if asset is None:
            self.recorder.record_agent_event(
                session_id,
                {
                    "event": "omni.visual_frame.missing",
                    "provider": self.omni_config.provider,
                    "frame_index": frame_index,
                    "timeout_seconds": timeout_seconds,
                    "source": "asset_request",
                },
            )
            return
        if not self._can_append_visual_frame(session_id=session_id, generation=generation):
            self._record_visual_frame_discarded(
                session_id=session_id,
                frame_index=frame_index,
                reason="visual_turn_inactive_after_asset",
                generation=generation,
                asset_id=asset.asset_id,
            )
            return
        provider = existing[1]
        appended_ids = self._visual_appended_asset_ids_by_session.setdefault(session_id, set())
        if asset.asset_id in appended_ids:
            return
        appended_ids.add(asset.asset_id)
        OmniVisualAppender(
            asset_service=self.asset_service,
            recorder=self.recorder,
            provider_name=self.omni_config.provider,
            default_direction=self.omni_config.visual_direction,
        ).append_agent_inline(
            provider=provider,
            asset=asset,
            context=VisualAppendContext(user_id=user_id, session_id=session_id),
            frame_index=frame_index,
        )

    def _capture_provider_message(self, *, user_id: str, session_id: str, record: dict[str, Any]) -> None:
        """把 Omni provider 的可读文本同步进用户级 messages。

        主要逻辑：Realtime 对话虽然输入输出是音频流，但 provider 仍会返回用户语音转写
        和助手输出 transcript/text。这里把这些文本保存到 `messages.jsonl`，便于开发者
        调试完整 messages，而不是只看音频事件。
        参数：`user_id/session_id/record` 描述 provider 事件。
        返回值：无。
        异常情况：未注入 ControlService 时跳过。
        """

        if self.control_service is None:
            return
        event = str(record.get("event") or "")
        if event == "omni.conversation.item.input_audio_transcription.completed":
            transcript = str(record.get("transcript") or "").strip()
            if not transcript:
                return
            key = (session_id, transcript)
            if key in self._recorded_user_transcripts:
                return
            self._recorded_user_transcripts.add(key)
            self.control_service.append_message(
                user_id,
                {
                    "session_id": session_id,
                    "role": "user",
                    "content": transcript,
                    "event": "input_transcript.done",
                    "source": "omni_realtime",
                },
            )
            return
        if event in {"omni.response.audio_transcript.delta", "omni.response.vision.delta", "omni.response.output_text.delta"}:
            if self._is_response_inactive(session_id=session_id, response_key=self._response_key_from_record(record)):
                return
            delta = str(record.get("delta") or record.get("text") or "")
            if delta:
                self._assistant_text_by_session.setdefault(session_id, []).append(delta)
            return
        if event in {"omni.response.audio_transcript.done", "omni.response.vision.done", "omni.response.output_text.done"}:
            if self._is_response_inactive(session_id=session_id, response_key=self._response_key_from_record(record)):
                self._assistant_text_by_session.pop(session_id, None)
                self.recorder.record_agent_event(
                    session_id,
                    {
                        "event": "omni.response.message_suppressed_after_interrupt",
                        "reason": "interrupted_response",
                    },
                )
                return
            content = str(record.get("transcript") or record.get("text") or "").strip()
            if not content:
                content = "".join(self._assistant_text_by_session.get(session_id, [])).strip()
            if not content:
                return
            self._assistant_text_by_session.pop(session_id, None)
            self.control_service.append_message(
                user_id,
                {
                    "session_id": session_id,
                    "role": "assistant",
                    "content": content,
                    "event": "assistant_text.done",
                    "source": "omni_realtime",
                },
            )

    def _append_tool_messages(
        self,
        *,
        user_id: str,
        session_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: Any,
        result: dict[str, Any],
    ) -> None:
        """把 Realtime 工具调用和工具结果写入用户级 messages。

        主要逻辑：Omni 的 function call 不走 VisionRealtimeAgentCore 的 message builder，
        因此需要在工具调用完成时补齐 assistant tool_call 和 tool result 两类消息。
        参数：工具调用 ID、名称、参数和结果。
        返回值：无。
        异常情况：未注入 ControlService 时跳过。
        """

        if self.control_service is None:
            return
        self.control_service.append_message(
            user_id,
            {
                "session_id": session_id,
                "role": "assistant",
                "content": "",
                "event": "assistant_tool_call.done",
                "source": "omni_realtime",
                "tool_calls": [
                    {
                        "id": tool_call_id,
                        "name": tool_name,
                        "arguments": arguments,
                    }
                ],
            },
        )
        self.control_service.append_message(
            user_id,
            {
                "session_id": session_id,
                "role": "tool",
                "content": result,
                "event": "tool_result.done",
                "source": "omni_realtime",
                "tool_call_id": tool_call_id,
                "name": tool_name,
            },
        )

    def _record_event(self, event: str, *, user_id: str, session_id: str, **payload) -> None:
        """记录统一 Agent 事件到内存和 runs。"""

        self._event_buffer.record_event(event, user_id=user_id, session_id=session_id, payload=payload)
        self.recorder.record_agent_event(session_id, {"event": event, "user_id": user_id, **payload})

    def _set_turn_state(self, user_id: str, session_id: str, state: str, *, reason: str, provider_event: str = "") -> None:
        """记录 Realtime/Omni 链路的统一 turn 状态事件。"""

        previous = self._state_by_session.get(session_id, "listening")
        if previous == state:
            return
        self._state_by_session[session_id] = state
        self._record_event(
            "agent.turn_state.changed",
            user_id=user_id,
            session_id=session_id,
            agent_core="OmniRealtimeAgentCore",
            modality="omni",
            provider=self.omni_config.provider,
            previous_state=previous,
            state=state,
            reason=reason,
            provider_event=provider_event or None,
        )

    def _record_system_error(
        self,
        *,
        user_id: str,
        session_id: str,
        message: str,
        record: dict[str, Any],
    ) -> None:
        """写入 provider/system 错误，避免异常在热路径重复刷屏。"""

        detail = dict(record)
        component = str(detail.pop("component", "") or "OmniRealtimeAgentCore")
        record_agent_recovery_error(
            recorder=self.recorder,
            event_buffer=self._event_buffer,
            control_service=self.control_service,
            user_id=user_id,
            session_id=session_id,
            component=component,
            message=message,
            agent_event="session.error",
            recoverable=True,
            fallback_text=DEFAULT_RECOVERABLE_ERROR_MESSAGE,
            record=detail,
        )


def _summarize_omni_event(message: dict[str, Any]) -> dict[str, Any]:
    event_type = str(message.get("type") or "unknown")
    record: dict[str, Any] = {"event": f"omni.{event_type}", "provider": "qwen"}
    response_id = _omni_response_id(message)
    if response_id:
        record["response_id"] = response_id
    if event_type == "response.audio_transcript.delta":
        record["delta"] = message.get("delta")
    elif event_type == "response.audio_transcript.done":
        record["transcript"] = message.get("transcript")
    elif event_type in {"response.vision.delta", "response.output_text.delta"}:
        record["delta"] = message.get("delta") or message.get("text")
    elif event_type in {"response.vision.done", "response.output_text.done"}:
        record["text"] = message.get("text") or message.get("transcript")
    elif event_type == "conversation.item.input_audio_transcription.completed":
        record["transcript"] = message.get("transcript")
    elif event_type == "response.done":
        response = message.get("response") if isinstance(message.get("response"), dict) else {}
        record["status"] = response.get("status")
    elif event_type == "response.output_item.added":
        item = message.get("item") if isinstance(message.get("item"), dict) else {}
        record["item_type"] = item.get("type")
        record["tool_call_id"] = item.get("call_id") or item.get("id")
        record["tool_name"] = item.get("name")
    elif event_type in {"response.function_call_arguments.delta", "response.tool_call_arguments.delta"}:
        record["tool_call_id"] = message.get("call_id") or message.get("item_id") or message.get("id")
        record["delta_len"] = len(str(message.get("delta") or message.get("arguments_delta") or ""))
    elif event_type in {"response.function_call_arguments.done", "response.tool_call.done"}:
        record["tool_call_id"] = message.get("call_id") or message.get("item_id") or message.get("id")
        record["tool_name"] = message.get("name")
    elif event_type == "response.output_item.done":
        item = message.get("item") if isinstance(message.get("item"), dict) else {}
        record["item_type"] = item.get("type")
        record["tool_call_id"] = item.get("call_id") or item.get("id")
        record["tool_name"] = item.get("name")
    return record


def _omni_response_id(message: dict[str, Any]) -> str:
    """从 Omni provider 原始事件中提取 response id。"""

    response = message.get("response") if isinstance(message.get("response"), dict) else {}
    item = message.get("item") if isinstance(message.get("item"), dict) else {}
    return str(message.get("response_id") or response.get("id") or item.get("response_id") or "")
