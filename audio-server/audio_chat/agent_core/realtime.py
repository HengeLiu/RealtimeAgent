from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Protocol

from audio_chat.agent_core.base import AgentEventBuffer, AgentCoreEvent
from audio_chat.agent_core.recovery import DEFAULT_RECOVERABLE_ERROR_MESSAGE, record_agent_recovery_error
from audio_chat.control import ControlService
from audio_chat.observability import RunRecorder
from audio_chat.output import OutputService
from audio_chat.output.service import OutputItem
from audio_chat.protocol import StreamChunk, StreamFormat
from audio_chat.tools import ToolGateway

TEXT_ONLY_VISION_TOOLS = {"interpret_current_view", "interpret_image"}


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
    instructions: str = "你是中文语音助手。请用简短口语回答用户。"
    tools: list[dict[str, Any]] = field(default_factory=list)


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
    tool_call_delta: Callable[[dict[str, Any]], None] | None = None
    tool_call_done: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    replay_audio_for_tool_result: Callable[[dict[str, Any]], list[bytes]] | None = None


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
        self._output_modalities: list[Any] = []
        self._completed_tool_call_ids: set[str] = set()
        self._pending_tool_followup_response: dict[str, Any] | None = None
        self._suppress_current_response_audio = False
        self._current_response_audio_emitted = False

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
            "instructions": self.config.instructions,
        }
        if self.config.tools:
            session_update_kwargs["tools"] = self.config.tools
        self._conversation.update_session(**session_update_kwargs)
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
            }
        )

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
        if chunk.payload:
            self._conversation.append_audio(base64.b64encode(chunk.payload).decode("ascii"))
        if chunk.final:
            self.commit_input(user_id=chunk.user_id, session_id=chunk.session_id, reason="final_chunk")

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
            self._output_modalities = []
            self._completed_tool_call_ids.clear()
            self._pending_tool_followup_response = None
            self._suppress_current_response_audio = False
            self._current_response_audio_emitted = False

    def _handle_provider_event(self, message: dict[str, Any]) -> None:
        callbacks = self._callbacks
        if callbacks is None:
            return
        event_type = str(message.get("type") or "")
        if event_type == "response.created":
            self._suppress_current_response_audio = False
            self._current_response_audio_emitted = False
            callbacks.provider_event(_summarize_omni_event(message))
            return
        if event_type == "response.output_item.added":
            item = message.get("item") if isinstance(message.get("item"), dict) else {}
            if item.get("type") in {"function_call", "tool_call"}:
                self._suppress_current_response_audio = True
            callbacks.provider_event(_summarize_omni_event(message))
            return
        if event_type == "response.audio.delta":
            raw_delta = str(message.get("delta") or "")
            callbacks.provider_event(
                {
                    "event": "omni.response.audio.delta",
                    "provider": "qwen",
                    "delta_base64_len": len(raw_delta),
                    "suppressed": self._suppress_current_response_audio,
                }
            )
            if raw_delta:
                audio = base64.b64decode(raw_delta)
                if self._suppress_current_response_audio:
                    callbacks.provider_event(
                        {
                            "event": "omni.response.audio.delta.suppressed",
                            "provider": "qwen",
                            "audio_bytes": len(audio),
                            "reason": "tool_call_response",
                        }
                    )
                    return
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
                self._current_response_audio_emitted = True
            return
        if event_type == "response.audio.done":
            callbacks.provider_event({"event": "omni.response.audio.done", "provider": "qwen"})
            if self._current_response_audio_emitted:
                callbacks.audio_done({"provider": "qwen", "model": self.config.model, "provider_event": event_type})
            return
        if event_type == "response.done":
            callbacks.provider_event(_summarize_omni_event(message))
            self._create_pending_tool_followup_response()
            self._suppress_current_response_audio = False
            self._current_response_audio_emitted = False
            return
        if event_type in {"response.function_call_arguments.delta", "response.tool_call_arguments.delta"}:
            self._suppress_current_response_audio = True
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
            self._suppress_current_response_audio = True
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
            callbacks.error(str(message.get("message") or message), {"provider": "qwen", "raw": message})
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
            response_instructions = _tool_result_followup_instructions(self.config.instructions, result)
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
                        append_video(base64.b64encode(image_bytes).decode("ascii"))
                        commit = getattr(self._conversation, "commit", None)
                        if callable(commit):
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
                        response_instructions = _capture_photo_response_instructions(self.config.instructions)
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
        audio_chat_root = Path(__file__).resolve().parents[3]
        candidates.extend(
            [
                Path.cwd() / path,
                audio_chat_root / path,
                audio_chat_root.parent / path,
            ]
        )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _capture_photo_response_instructions(base: str) -> str:
    """构造 capture_photo 后续响应指令。"""

    return (
        f"{base}\n\n"
        "刚刚通过 capture_photo 工具提交了一张新的实时照片。"
        "本次回答必须只基于刚提交的这张照片回答用户上一轮视觉问题；"
        "如果它和历史照片或历史描述冲突，以刚提交的新照片为准。"
        "刚才已经完成抓拍，本次不要再次调用 capture_photo。"
        "不要复述工具参数、文件名或调用过程；看不清时直接说明看不清，不能猜测。"
    )


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
    return (
        f"{base}\n\n"
        "刚刚的工具调用失败了。"
        f"工具名：{name}。失败原因：{message}。"
        "本次回答必须把失败事实直接告知用户，不能声称工具已经执行成功。"
        f"{task_rule}"
        "请用简短口语中文说明，并在合适时建议用户重试。"
    )


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

        if self.tool_gateway is None:
            return []
        return [
            schema
            for schema in self.tool_gateway.provider_schemas()
            if _provider_tool_schema_name(schema) not in TEXT_ONLY_VISION_TOOLS
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
                {"event": "realtime.tool_call.committed", "tool_call_id": tool_call_id, "tool_name": name},
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
        control_service: ControlService | None = None,
        realtime_config: RealtimeProviderConfig | None = None,
        provider_factory: Callable[[RealtimeProviderConfig], RealtimeProviderAdapter] | None = None,
        tool_gateway: ToolGateway | None = None,
        memory_service: Any = None,
        max_context_messages: int = 30,
        **_: Any,
    ) -> None:
        self.output_service = output_service
        self.recorder = recorder
        self.control_service = control_service
        self.realtime_config = realtime_config or RealtimeProviderConfig()
        self.memory_service = memory_service
        self.max_context_messages = max(1, int(max_context_messages or 30))
        self.provider_factory = provider_factory or self._default_provider_factory
        self.output_adapter = RealtimeOutputAdapter(output_service=output_service, recorder=recorder)
        self.tool_bridge = RealtimeToolBridge(tool_gateway=tool_gateway, recorder=recorder)
        self._sessions: dict[str, tuple[str, RealtimeProviderAdapter]] = {}
        self._failed_sessions: set[str] = set()
        self._sessions_with_provider_output: set[str] = set()
        self._event_buffer = AgentEventBuffer()
        self._assistant_text_by_session: dict[str, list[str]] = {}
        self._recorded_user_transcripts: set[tuple[str, str]] = set()
        self._active_user_audio_by_session: dict[str, list[bytes]] = {}
        self._last_user_audio_by_session: dict[str, list[bytes]] = {}

    def bind_tool_gateway(self, tool_gateway: ToolGateway) -> None:
        """绑定 Realtime provider 工具桥使用的 ToolGateway。"""

        self.tool_bridge.bind_tool_gateway(tool_gateway)

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
        tools = self._realtime_tool_schemas()
        history_messages = self._load_runtime_messages(user_id=user_id, session_id=session_id)
        instructions = self._build_instructions(user_id=user_id)
        summary_fragment = self._load_message_summary_fragment(user_id=user_id, session_id=session_id)
        if summary_fragment:
            instructions = f"{instructions}\n\n{summary_fragment}"
        session_config = replace(self.realtime_config, tools=tools, instructions=instructions)
        self.recorder.record_model_request(
            session_id,
            self._build_model_request(
                user_id=user_id,
                session_id=session_id,
                config=session_config,
                tools=tools,
                history_messages=history_messages,
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
            tool_count=len(tools),
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
            )
            or self._sessions_with_provider_output.add(session_id),
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
                "event": "realtime.input_audio.replay.prepared",
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
                "event": "realtime.tool_result.ready",
                "tool_call_id": result.get("tool_call_id"),
                "tool_name": result.get("name"),
                "ok": result.get("ok"),
                "provider_result_injection": "handled_by_provider_adapter",
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
            if _provider_tool_schema_name(schema) in TEXT_ONLY_VISION_TOOLS:
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
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": config.instructions},
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
        return {
            "provider": config.provider,
            "model": config.model,
            "runner": "agent_core_realtime_audio",
            "instructions": config.instructions,
            "messages": messages,
            "active_history_message_count": len(history),
            "active_history_injected_to": "messages" if history else "",
            "tools": tools,
            "tool_count": len(tools),
            "user_id": user_id,
            "session_id": session_id,
        }

    def _build_instructions(self, *, user_id: str) -> str:
        """构造当前 realtime 会话 instructions。

        主要逻辑：在静态 Omni 指令后追加长期记忆片段，让模型直接获得当前用户的已保存信息。
        参数：`user_id` 为当前用户编号。
        返回值：发送给 Realtime provider 的 instructions。
        异常情况：memory 未启用或读取失败时只返回基础指令。
        """

        base = self.realtime_config.instructions
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

    def _record_provider_event(self, *, user_id: str, session_id: str, record: dict[str, Any]) -> None:
        """记录 provider 事件到 runs 和统一事件缓存。"""

        self.recorder.record_agent_event(session_id, record)
        self._capture_provider_message(user_id=user_id, session_id=session_id, record=record)
        self._event_buffer.record_event(
            str(record.get("event") or "provider.event"),
            user_id=user_id,
            session_id=session_id,
            payload=dict(record),
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
        if event in {"omni.response.audio_transcript.delta", "omni.response.text.delta", "omni.response.output_text.delta"}:
            delta = str(record.get("delta") or record.get("text") or "")
            if delta:
                self._assistant_text_by_session.setdefault(session_id, []).append(delta)
            return
        if event in {"omni.response.audio_transcript.done", "omni.response.text.done", "omni.response.output_text.done"}:
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

        主要逻辑：Omni 的 function call 不走 TextAgentCore 的 message builder，
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
        component = str(detail.pop("component", "") or "RealtimeAudioAgentCore")
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
    if event_type == "response.audio_transcript.delta":
        record["delta"] = message.get("delta")
    elif event_type == "response.audio_transcript.done":
        record["transcript"] = message.get("transcript")
    elif event_type in {"response.text.delta", "response.output_text.delta"}:
        record["delta"] = message.get("delta") or message.get("text")
    elif event_type in {"response.text.done", "response.output_text.done"}:
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
