"""连续对话窗口关闭管理器。"""

from __future__ import annotations

import threading
from typing import Any, Callable

from infra.logging import LogContext, log_debug, log_info
from runtime.voice_state import PlaybackStreamContext, SegmentBuffer, VoiceSessionController


def _format_dialog_text(text: str, *, max_chars: int = 240) -> str:
    """格式化连续对话日志文本。

    主要逻辑：
    1. 把换行和多余空白压缩成单个空格。
    2. 控制最大长度，避免日志过长。

    参数：
    1. `text`：原始文本。
    2. `max_chars`：最多保留的字符数。

    返回值：
    1. 可写入单行日志的文本。

    异常情况：
    1. 本函数不抛出业务异常。
    """

    compact = " ".join(text.split())
    if max_chars <= 0 or len(compact) <= max_chars:
        return compact
    return f"{compact[:max_chars]}..."


class ContinuousDialogManager:
    """管理端侧连续对话窗口关闭。

    主要功能：
    1. 统一下发 `voice.dialog.close` 控制消息。
    2. 支持模型工具请求在当前回复播放完成后再关闭窗口。
    3. 支持停止指令立即清理本轮 Omni 会话和播放资源。

    主要方法：
    1. `close_for_stop_command`：按用户停止指令立即关闭。
    2. `schedule_after_reply`：按模型工具请求登记延迟关闭。
    3. `close_after_playback_if_needed`：播放完成时执行登记的关闭。

    主要属性：
    1. `controllers`：设备语音控制器集合。
    2. `send_control_message`：控制面消息发送函数。
    3. `handle_user_interrupt/discard_utterance_photo`：运行时清理回调。
    """

    def __init__(
        self,
        *,
        lock: threading.Lock,
        controllers: dict[str, VoiceSessionController],
        send_control_message: Callable[[str, str, str, str, dict[str, Any]], None],
        handle_user_interrupt: Callable[..., None],
        discard_utterance_photo: Callable[..., None],
        logger,
    ) -> None:
        """初始化连续对话管理器。

        主要逻辑：
        1. 保存共享控制器、控制消息发送函数和清理回调。

        参数：
        1. `lock/controllers`：运行时共享会话状态。
        2. `send_control_message`：控制消息发送函数。
        3. `handle_user_interrupt`：中断当前播放的回调。
        4. `discard_utterance_photo`：丢弃本轮自动照片的回调。
        5. `logger`：运行时日志对象。

        返回值：
        1. 无返回值。

        异常情况：
        1. 初始化不访问外部系统，不主动抛出业务异常。
        """

        self._lock = lock
        self._controllers = controllers
        self._send_control_message = send_control_message
        self._handle_user_interrupt = handle_user_interrupt
        self._discard_utterance_photo = discard_utterance_photo
        self._logger = logger

    @staticmethod
    def extract_close_request(meta: dict[str, Any]) -> dict[str, Any] | None:
        """从 Agent 结果中读取模型工具声明的连续对话关闭意图。

        主要逻辑：
        1. 只接受 `turn_meta.close_continuous_dialog.scheduled=true` 的结构。

        参数：
        1. `meta`：Agent 返回的元数据。

        返回值：
        1. 命中时返回关闭请求，否则返回 None。

        异常情况：
        1. 元数据结构不符合预期时返回 None。
        """

        turn_meta = meta.get("turn_meta")
        if not isinstance(turn_meta, dict):
            return None
        request = turn_meta.get("close_continuous_dialog")
        return request if isinstance(request, dict) and request.get("scheduled") else None

    def send_close_control(
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
        1. 统一封装 `voice.dialog.close` 的 payload。
        2. 清理控制器上的 persistent Omni 会话引用。
        3. 关闭 persistent Omni 会话，但不修改播放队列。

        参数：
        1. `device_id/session_id`：当前设备和会话编号。
        2. `reason/source`：关闭原因和来源。
        3. `stream_id`：可选，下行播放流编号。

        返回值：
        1. 无返回值。

        异常情况：
        1. 关闭旧 Omni 会话失败时写 DEBUG，不阻止控制消息下发。
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
            try:
                persistent_session.close(blocking=False)
            except TypeError:
                persistent_session.close()
            except Exception as exc:  # noqa: BLE001 - 关闭失败不能阻止端侧窗口关闭
                log_debug(
                    self._logger,
                    f"关闭 persistent Omni 会话失败: reason={exc!r}",
                    LogContext(device_id=device_id, session_id=session_id),
                )
        self._send_control_message(
            device_id,
            "request",
            "voice.dialog.close",
            session_id,
            payload,
        )

    def close_for_stop_command(
        self,
        *,
        device_id: str,
        session_id: str,
        segment: SegmentBuffer,
        transcript: str,
        source: str,
    ) -> None:
        """按用户停止指令关闭连续对话窗口并清理本轮资源。

        主要逻辑：
        1. 关闭本轮 Omni 预连接，避免停止指令继续生成模型回复。
        2. 如果已有播放流被提前创建，则通过用户打断路径中断播放。
        3. 丢弃本轮自动照片，并向眼镜下发 `voice.dialog.close`。

        参数：
        1. `device_id/session_id`：当前设备和会话编号。
        2. `segment`：当前语音段。
        3. `transcript/source`：停止指令文本和来源。

        返回值：
        1. 无返回值。

        异常情况：
        1. Omni 会话清理失败只写 DEBUG。
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
                if (
                    controller is not None
                    and controller.persistent_omni_realtime_session is segment.omni_realtime_session
                ):
                    controller.persistent_omni_realtime_session = None
        if segment.omni_realtime_context is not None:
            self._handle_user_interrupt(
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
                f"source={source} transcript={_format_dialog_text(transcript)!r}"
            ),
            LogContext(device_id=device_id, session_id=session_id),
        )

    def schedule_after_reply(
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
        3. 关闭请求不打断当前回复。
        """

        reason = str(request.get("reason") or "model_requested").strip() or "model_requested"
        source = str(request.get("source") or "model_tool").strip() or "model_tool"
        if playback.finished_event.is_set() or playback.completed:
            self.send_close_control(
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

    def close_after_playback_if_needed(
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
        self.send_close_control(
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
