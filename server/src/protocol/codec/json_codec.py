"""ControlMessage JSON 编解码。"""

from __future__ import annotations

import json
from typing import Any

from infra.errors import ErrorCode, build_error
from protocol.messages.control_message import ControlMessage


class JsonMessageCodec:
    """控制消息 JSON 编解码器。

    主要功能：
    1. 将 `ControlMessage` 编码为 JSON 字节。
    2. 将 JSON 字节解码为 `ControlMessage`。
    """

    def encode(self, message: ControlMessage) -> bytes:
        """编码控制消息。

        参数：
        1. `message`：控制消息对象。

        返回值：
        1. UTF-8 JSON 字节。

        异常情况：
        1. 序列化失败时抛出 `AppError(DECODE_ERROR)`。
        """

        message.validate()
        try:
            return json.dumps(message.to_dict(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise build_error(
                ErrorCode.DECODE_ERROR,
                "ControlMessage JSON 编码失败",
                details={"reason": str(exc)},
            ) from exc

    def decode(self, raw: bytes | str | dict[str, Any]) -> ControlMessage:
        """解码控制消息。

        参数：
        1. `raw`：原始数据，支持字节、字符串或字典。

        返回值：
        1. `ControlMessage` 对象。

        异常情况：
        1. JSON 非法或字段不合法时抛出结构化错误。
        """

        if isinstance(raw, dict):
            obj = raw
        else:
            text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            try:
                obj = json.loads(text)
            except json.JSONDecodeError as exc:
                raise build_error(
                    ErrorCode.DECODE_ERROR,
                    "ControlMessage JSON 解码失败",
                    details={"reason": str(exc)},
                ) from exc
        return ControlMessage.from_dict(obj)
