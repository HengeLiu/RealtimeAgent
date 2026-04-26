"""控制消息生成工具。"""

from __future__ import annotations

import time
import uuid
from typing import Any

from protocol.messages.control_message import ControlMessage, Endpoint


def build_message_id() -> str:
    """生成消息编号。

    返回值：
    1. `msg_` 开头的唯一字符串。
    """

    return f"msg_{uuid.uuid4().hex}"


def now_ms() -> int:
    """返回当前毫秒时间戳。

    返回值：
    1. 当前时间对应的毫秒整数。
    """

    return int(time.time() * 1000)


def create_control_message(
    *,
    semantic: str,
    name: str,
    source: Endpoint,
    target: Endpoint,
    payload: dict[str, Any],
    trace_id: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    stream_id: str | None = None,
    message_id: str | None = None,
) -> ControlMessage:
    """创建控制消息对象。

    主要逻辑：
    1. 自动填充 `message_id` 与 `ts`。
    2. 构造消息后立即执行校验。

    参数：
    1. `semantic`：语义类型。
    2. `name`：消息名。
    3. `source`：发送端点。
    4. `target`：接收端点。
    5. `payload`：业务负载。
    6. `trace_id/session_id/task_id/stream_id/message_id`：可选上下文字段。

    返回值：
    1. `ControlMessage` 对象。
    """

    message = ControlMessage(
        message_id=message_id or build_message_id(),
        channel="control",
        semantic=semantic,
        name=name,
        source=source,
        target=target,
        ts=now_ms(),
        payload=payload,
        trace_id=trace_id,
        session_id=session_id,
        task_id=task_id,
        stream_id=stream_id,
    )
    message.validate()
    return message
