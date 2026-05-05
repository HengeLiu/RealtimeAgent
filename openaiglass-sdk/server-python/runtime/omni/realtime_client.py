"""百炼 Omni Realtime 语音直出客户端。"""

from __future__ import annotations

import base64
import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from infra.config import ServerSettings
from infra.errors import AppError, ErrorCode, build_error
from infra.logging import LogContext, get_logger, log_debug, log_info
from runtime.voice_constants import (
    MODEL_OUTPUT_SAMPLE_RATE_HZ,
    OMNI_SEMANTIC_VAD_AUTO_RESPONSE_GRACE_SECONDS,
    OMNI_SEMANTIC_VAD_NO_AUTO_RESPONSE,
)
from runtime.voice_models import ModelChunk
from runtime.omni.tool_bridge import OmniToolBridge


def _format_log_text(text: str, *, max_chars: int = 240) -> str:
    """格式化适合写入单行日志的文本。"""

    compact = " ".join(text.split())
    if max_chars <= 0 or len(compact) <= max_chars:
        return compact
    return f"{compact[:max_chars]}..."


def _summarize_omni_server_event(message: dict[str, Any]) -> str:
    """生成 Omni Realtime 服务端事件的安全日志摘要。"""

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

    def discard_pending_input(self) -> None:
        """丢弃当前长连接上已经 append 但未提交的输入音频。

        主要用途：
        1. Omni semantic VAD 没有为一段连续 VAD 音频自动提交响应时，服务端需要忽略该 turn。
        2. persistent 长连接不能因此关闭，否则端侧连续窗口仍在但模型连接已断。
        3. DashScope SDK 提供 `clear_appended_audio()` 清理未提交 input buffer。

        异常情况：
        1. 会话已经关闭或 SDK 清理失败时抛出结构化异常，由调用方决定是否重建长连接。
        """

        if self._closed:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "Omni Realtime 长连接已经关闭，不能清理未提交输入",
                details={"segment_id": self._segment_id, "stream_id": self._stream_id},
            )
        clear_appended_audio = getattr(self._conversation, "clear_appended_audio", None)
        if not callable(clear_appended_audio):
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "当前 DashScope SDK 不支持清理 Omni Realtime 未提交输入",
                details={"method": "clear_appended_audio"},
            )
        try:
            clear_appended_audio()
        except Exception as exc:
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                "清理 Omni Realtime 未提交输入失败",
                details={"reason": str(exc), "segment_id": self._segment_id, "stream_id": self._stream_id},
            ) from exc
        self._audio_bytes = 0
        self._audio_frame_count = 0
        self._image_frame_count = 0
        self._first_audio_append_at_ms = None

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

        self_logger = self._logger
        tool_bridge = OmniToolBridge(
            tool_handler_getter=lambda: tool_handler_box[0],
            pending_tool_lock=pending_tool_lock,
            pending_tool_count_box=pending_tool_count_box,
            error_box=error_box,
            done_event=done_event,
            logger=self_logger,
            device_id=device_id,
            session_id=session_id,
        )

        def _complete_tool_call(*, call_id: str, tool_name: str, arguments_text: str) -> None:
            """执行 Realtime 工具调用并把结果回填给 Omni。"""

            tool_bridge.complete_tool_call(
                conversation=conversation,
                multimodality=MultiModality,
                call_id=call_id,
                tool_name=tool_name,
                arguments_text=arguments_text,
            )

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
