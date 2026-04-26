"""媒体帧编解码实现。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from infra.errors import ErrorCode, build_error


@dataclass(slots=True)
class MediaFrame:
    """统一媒体帧对象。

    主要功能：
    1. 封装 `header_json + payload` 形式的二进制媒体帧。
    2. 负责媒体帧头字段校验与二进制编码。

    主要属性：
    1. `header`：帧头字典，至少应包含 `stream_id/frame_type/seq/ts_ms/codec/payload_size/final`。
    2. `payload`：真实媒体字节。
    """

    header: dict[str, Any]
    payload: bytes

    def validate(self) -> None:
        """校验媒体帧字段。

        主要逻辑：
        1. 校验帧头与负载类型。
        2. 校验帧头必填字段存在。
        3. 校验 `payload_size` 与实际字节长度一致。

        异常情况：
        1. 任一校验失败时抛出 `AppError(INVALID_MESSAGE)`。
        """

        if not isinstance(self.header, dict):
            raise build_error(ErrorCode.INVALID_MESSAGE, "MediaFrame.header 必须是对象")
        if not isinstance(self.payload, (bytes, bytearray)):
            raise build_error(ErrorCode.INVALID_MESSAGE, "MediaFrame.payload 必须是字节数组")

        required_fields = [
            "version",
            "stream_id",
            "frame_type",
            "seq",
            "ts_ms",
            "codec",
            "payload_size",
            "final",
        ]
        missing = [field for field in required_fields if field not in self.header]
        if missing:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "MediaFrame.header 缺少必填字段",
                details={"missing_fields": missing},
            )

        payload_size = self.header.get("payload_size")
        if not isinstance(payload_size, int) or payload_size < 0:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "payload_size 必须是非负整数",
                details={"payload_size": payload_size},
            )

        if payload_size != len(self.payload):
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "payload_size 与真实负载长度不一致",
                details={"payload_size": payload_size, "actual": len(self.payload)},
            )

    def encode(self) -> bytes:
        """将媒体帧编码为二进制数据。

        主要逻辑：
        1. 先校验对象。
        2. 对帧头进行 JSON 编码。
        3. 使用 `4字节大端长度 + 帧头字节 + 负载字节` 拼装。

        返回值：
        1. 编码后的二进制帧。

        异常情况：
        1. 帧头不可序列化时抛出 `AppError(DECODE_ERROR)`。
        """

        self.validate()
        try:
            header_bytes = json.dumps(self.header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise build_error(
                ErrorCode.DECODE_ERROR,
                "MediaFrame.header JSON 编码失败",
                details={"reason": str(exc)},
            ) from exc

        header_len = len(header_bytes)
        return header_len.to_bytes(4, byteorder="big", signed=False) + header_bytes + bytes(self.payload)

    @classmethod
    def decode(cls, raw: bytes) -> "MediaFrame":
        """从二进制数据解码媒体帧。

        参数：
        1. `raw`：完整二进制帧。

        返回值：
        1. `MediaFrame` 对象。

        异常情况：
        1. 长度不足、帧头 JSON 非法或校验失败时抛出结构化错误。
        """

        if len(raw) < 4:
            raise build_error(
                ErrorCode.DECODE_ERROR,
                "媒体帧长度不足，无法读取 header_len",
                details={"raw_len": len(raw)},
            )

        header_len = int.from_bytes(raw[:4], byteorder="big", signed=False)
        if header_len <= 0:
            raise build_error(
                ErrorCode.DECODE_ERROR,
                "header_len 必须大于 0",
                details={"header_len": header_len},
            )
        if len(raw) < 4 + header_len:
            raise build_error(
                ErrorCode.DECODE_ERROR,
                "媒体帧长度不足，无法读取完整 header_json",
                details={"raw_len": len(raw), "header_len": header_len},
            )

        header_bytes = raw[4 : 4 + header_len]
        payload = raw[4 + header_len :]
        try:
            header_obj = json.loads(header_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise build_error(
                ErrorCode.DECODE_ERROR,
                "header_json 解码失败",
                details={"reason": str(exc)},
            ) from exc

        frame = cls(header=header_obj, payload=payload)
        frame.validate()
        return frame
