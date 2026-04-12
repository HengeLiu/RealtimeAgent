"""统一控制消息模型定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from infra.errors import ErrorCode, build_error

SUPPORTED_SEMANTICS = {"request", "notify"}
SUPPORTED_CHANNELS = {"control"}


@dataclass(slots=True)
class Endpoint:
    """消息端点模型。

    主要功能：
    1. 表示消息发送方或接收方的设备身份与模块身份。

    主要属性：
    1. `device_id`：设备唯一编号。
    2. `device_type`：设备类型，例如 `glass` 或 `server`。
    3. `module`：模块名，例如 `glass-api`。
    """

    device_id: str
    device_type: str
    module: str

    def validate(self, *, field_name: str) -> None:
        """校验端点字段完整性。

        参数：
        1. `field_name`：当前字段名，用于拼接报错信息。

        异常情况：
        1. 任一字段为空时抛出 `AppError(INVALID_MESSAGE)`。
        """

        if not self.device_id.strip():
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                f"{field_name}.device_id 不能为空",
            )
        if not self.device_type.strip():
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                f"{field_name}.device_type 不能为空",
            )
        if not self.module.strip():
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                f"{field_name}.module 不能为空",
            )

    def to_dict(self) -> dict[str, str]:
        """转换为字典。

        返回值：
        1. 标准端点字典。
        """

        return {
            "device_id": self.device_id,
            "device_type": self.device_type,
            "module": self.module,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, field_name: str) -> "Endpoint":
        """从字典构建端点对象。

        参数：
        1. `data`：端点原始字典。
        2. `field_name`：当前字段名，用于报错。

        返回值：
        1. `Endpoint` 对象。

        异常情况：
        1. 字段缺失或类型错误时抛出 `AppError(INVALID_MESSAGE)`。
        """

        if not isinstance(data, dict):
            raise build_error(ErrorCode.INVALID_MESSAGE, f"{field_name} 必须是对象")
        try:
            endpoint = cls(
                device_id=str(data["device_id"]),
                device_type=str(data["device_type"]),
                module=str(data["module"]),
            )
        except KeyError as exc:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                f"{field_name} 缺少必填字段",
                details={"missing_field": str(exc)},
            ) from exc
        endpoint.validate(field_name=field_name)
        return endpoint


@dataclass(slots=True)
class ControlMessage:
    """统一控制消息模型。

    主要功能：
    1. 承载控制面 request/notify 消息。
    2. 统一字段校验，确保链路上消息结构稳定。

    主要属性：
    1. `message_id`：消息唯一编号。
    2. `channel`：通道名，首版固定 `control`。
    3. `semantic`：语义类型，取值 `request` 或 `notify`。
    4. `name`：消息名，例如 `voice.session.open`。
    5. `source`：发送端点。
    6. `target`：接收端点。
    7. `ts`：毫秒时间戳。
    8. `payload`：业务负载。
    9. `version/trace_id/session_id/task_id/stream_id/priority/reply_to/meta`：按需字段。
    """

    message_id: str
    channel: str
    semantic: str
    name: str
    source: Endpoint
    target: Endpoint
    ts: int
    payload: dict[str, Any]
    version: str | None = "v1"
    trace_id: str | None = None
    session_id: str | None = None
    task_id: str | None = None
    stream_id: str | None = None
    priority: str | None = None
    reply_to: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        """校验控制消息字段合法性。

        主要逻辑：
        1. 校验必填字段非空。
        2. 校验 `semantic` 与 `channel` 白名单。
        3. 校验 `payload/meta` 类型。

        异常情况：
        1. 任一校验失败时抛出 `AppError(INVALID_MESSAGE)`。
        """

        if not self.message_id.strip():
            raise build_error(ErrorCode.INVALID_MESSAGE, "message_id 不能为空")
        if self.channel not in SUPPORTED_CHANNELS:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "channel 非法",
                details={"channel": self.channel, "supported": sorted(SUPPORTED_CHANNELS)},
            )
        if self.semantic not in SUPPORTED_SEMANTICS:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "semantic 非法",
                details={"semantic": self.semantic, "supported": sorted(SUPPORTED_SEMANTICS)},
            )
        if not self.name.strip():
            raise build_error(ErrorCode.INVALID_MESSAGE, "name 不能为空")
        if not isinstance(self.ts, int) or self.ts <= 0:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "ts 必须是正整数毫秒时间戳",
                details={"ts": self.ts},
            )
        if not isinstance(self.payload, dict):
            raise build_error(ErrorCode.INVALID_MESSAGE, "payload 必须是对象")
        if not isinstance(self.meta, dict):
            raise build_error(ErrorCode.INVALID_MESSAGE, "meta 必须是对象")
        self.source.validate(field_name="source")
        self.target.validate(field_name="target")

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。

        返回值：
        1. 可直接序列化的消息字典。
        """

        data: dict[str, Any] = {
            "version": self.version,
            "message_id": self.message_id,
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "stream_id": self.stream_id,
            "channel": self.channel,
            "semantic": self.semantic,
            "name": self.name,
            "source": self.source.to_dict(),
            "target": self.target.to_dict(),
            "priority": self.priority,
            "reply_to": self.reply_to,
            "ts": self.ts,
            "payload": self.payload,
            "meta": self.meta,
        }
        return {key: value for key, value in data.items() if value is not None}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ControlMessage":
        """从字典构建控制消息对象。

        参数：
        1. `data`：原始消息字典。

        返回值：
        1. `ControlMessage` 对象。

        异常情况：
        1. 缺失必填字段或字段类型错误时抛出 `AppError(INVALID_MESSAGE)`。
        """

        if not isinstance(data, dict):
            raise build_error(ErrorCode.INVALID_MESSAGE, "控制消息必须是对象")

        required_fields = [
            "message_id",
            "channel",
            "semantic",
            "name",
            "source",
            "target",
            "ts",
            "payload",
        ]
        missing = [field for field in required_fields if field not in data]
        if missing:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "控制消息缺少必填字段",
                details={"missing_fields": missing},
            )

        message = cls(
            version=(str(data["version"]) if "version" in data else "v1"),
            message_id=str(data["message_id"]),
            trace_id=(str(data["trace_id"]) if data.get("trace_id") else None),
            session_id=(str(data["session_id"]) if data.get("session_id") else None),
            task_id=(str(data["task_id"]) if data.get("task_id") else None),
            stream_id=(str(data["stream_id"]) if data.get("stream_id") else None),
            channel=str(data["channel"]),
            semantic=str(data["semantic"]),
            name=str(data["name"]),
            source=Endpoint.from_dict(data["source"], field_name="source"),
            target=Endpoint.from_dict(data["target"], field_name="target"),
            priority=(str(data["priority"]) if data.get("priority") else None),
            reply_to=(str(data["reply_to"]) if data.get("reply_to") else None),
            ts=int(data["ts"]),
            payload=data["payload"],
            meta=(data["meta"] if isinstance(data.get("meta", {}), dict) else {}),
        )
        message.validate()
        return message
