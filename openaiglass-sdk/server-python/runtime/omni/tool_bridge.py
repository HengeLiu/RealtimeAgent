"""Omni Realtime 工具调用桥接层。

本模块负责把 Omni Realtime function calling 事件转成 SDK Tool 调用，并把工具
结果回填给同一条 Realtime 会话。视觉工具 `capture_photo` 返回图片时，也在
这里追加到会话，让模型继续基于图片回答。
"""

from __future__ import annotations

import base64
import json
import os
import threading
from typing import Any, Callable

from infra.logging import LogContext, log_debug


def read_capture_photo_tool_image(output_payload: dict[str, Any]) -> bytes | None:
    """从 `capture_photo` 工具结果中读取图片字节。

    主要逻辑：
    1. 只处理 `ok=true` 的工具结果。
    2. 从 `data.storage_uri` 中读取本地图片文件。
    3. 文件不存在或结构不匹配时返回 None，让模型只接收普通工具结果。

    参数：
    1. `output_payload`：SDK Tool 返回的结构化结果。

    返回值：
    1. 命中时返回图片字节，否则返回 None。

    异常情况：
    1. 文件读取异常会向外抛出，由调用方统一转成 Realtime 错误。
    """

    if not output_payload.get("ok"):
        return None
    data = output_payload.get("data")
    if not isinstance(data, dict):
        return None
    storage_uri = str(data.get("storage_uri") or "").strip()
    if not storage_uri or not os.path.isfile(storage_uri):
        return None
    with open(storage_uri, "rb") as image_file:
        return image_file.read()


class OmniToolBridge:
    """执行 Omni Realtime 工具调用并回填结果。

    主要功能：
    1. 调用 SDK 侧工具处理器。
    2. 通过 `conversation.create_item(function_call_output)` 回填工具结果。
    3. 对 `capture_photo` 工具追加图片到同一 Realtime 会话。
    4. 工具完成后继续创建文本和音频响应。

    主要属性：
    1. `tool_handler_getter`：运行时可更新的工具处理器读取回调。
    2. `pending_tool_lock/pending_tool_count_box`：工具并发计数状态。
    3. `error_box/done_event`：异常时通知等待方结束当前响应。
    """

    def __init__(
        self,
        *,
        tool_handler_getter: Callable[[], Callable[[dict[str, Any]], dict[str, Any]] | None],
        pending_tool_lock: threading.Lock,
        pending_tool_count_box: list[int],
        error_box: list[str],
        done_event: threading.Event,
        logger,
        device_id: str,
        session_id: str,
    ) -> None:
        """初始化 Omni 工具桥。

        主要逻辑：
        1. 保存工具处理器读取回调。
        2. 保存工具计数、错误传递和日志上下文。

        参数：
        1. `tool_handler_getter`：返回当前工具处理器的回调。
        2. `pending_tool_lock/pending_tool_count_box`：工具计数共享状态。
        3. `error_box/done_event`：错误通知共享状态。
        4. `logger/device_id/session_id`：日志上下文。

        返回值：
        1. 无返回值。

        异常情况：
        1. 初始化不访问外部系统，不抛出业务异常。
        """

        self._tool_handler_getter = tool_handler_getter
        self._pending_tool_lock = pending_tool_lock
        self._pending_tool_count_box = pending_tool_count_box
        self._error_box = error_box
        self._done_event = done_event
        self._logger = logger
        self._device_id = device_id
        self._session_id = session_id

    def complete_tool_call(
        self,
        *,
        conversation,
        multimodality,
        call_id: str,
        tool_name: str,
        arguments_text: str,
    ) -> None:
        """执行 Realtime 工具调用并把结果回填给 Omni。

        主要逻辑：
        1. 没有工具处理器时，构造结构化错误结果。
        2. 有工具处理器时，以 call_id、name、arguments 调用 SDK Tool 网关。
        3. 把结果作为 `function_call_output` 回填到 Realtime conversation。
        4. `capture_photo` 命中图片时追加图片，再请求模型继续文本和音频响应。

        参数：
        1. `conversation`：DashScope Realtime conversation 对象。
        2. `multimodality`：DashScope `MultiModality` 枚举。
        3. `call_id/tool_name/arguments_text`：Omni server event 中的工具调用信息。

        返回值：
        1. 无返回值。

        异常情况：
        1. 任何异常都会记录到 `error_box` 并触发 `done_event`。
        """

        try:
            handler = self._tool_handler_getter()
            if handler is None:
                output_payload = {
                    "ok": False,
                    "error": {
                        "code": "TOOL_HANDLER_NOT_CONFIGURED",
                        "message": "当前运行时没有配置工具调用处理器",
                    },
                }
            else:
                output_payload = handler(
                    {
                        "call_id": call_id,
                        "name": tool_name,
                        "arguments": arguments_text,
                    }
                )
            conversation.create_item(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(output_payload, ensure_ascii=False, default=str),
                }
            )
            if tool_name == "capture_photo":
                image_bytes = read_capture_photo_tool_image(output_payload)
                if image_bytes:
                    conversation.append_video(base64.b64encode(image_bytes).decode("ascii"))
                    log_debug(
                        self._logger,
                        (
                            "Omni Realtime 已追加 capture_photo 工具图片 "
                            f"tool_name={tool_name} call_id={call_id} bytes={len(image_bytes)}"
                        ),
                        LogContext(device_id=self._device_id, session_id=self._session_id, message_id="omni_realtime"),
                    )
            with self._pending_tool_lock:
                self._pending_tool_count_box[0] = max(self._pending_tool_count_box[0] - 1, 0)
            conversation.create_response(output_modalities=[multimodality.TEXT, multimodality.AUDIO])
            log_debug(
                self._logger,
                f"Omni Realtime 工具结果已回填 tool_name={tool_name} call_id={call_id}",
                LogContext(device_id=self._device_id, session_id=self._session_id, message_id="omni_realtime"),
            )
        except Exception as exc:  # noqa: BLE001 - 工具桥异常需要结束当前 Realtime 响应
            with self._pending_tool_lock:
                self._pending_tool_count_box[0] = max(self._pending_tool_count_box[0] - 1, 0)
            self._error_box.append(f"Omni Realtime 工具调用处理失败: {exc}")
            self._done_event.set()
