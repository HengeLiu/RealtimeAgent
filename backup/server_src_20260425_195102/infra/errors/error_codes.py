"""统一错误模型与错误码定义模块。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    """系统统一错误码枚举。

    主要功能：
    1. 统一服务端对外与内部错误编码，避免同类错误出现多个名字。
    2. 为日志检索与链路排障提供稳定字段。

    主要属性：
    1. `INVALID_MESSAGE`：消息结构或字段非法。
    2. `UNAUTHORIZED`：鉴权失败或无权限。
    3. `UNSUPPORTED_VERSION`：协议版本不支持。
    4. `DEVICE_BUSY`：设备正忙，无法执行新请求。
    5. `TASK_NOT_FOUND`：任务不存在。
    6. `STREAM_NOT_FOUND`：媒体流上下文不存在。
    7. `TIMEOUT`：处理超时。
    8. `INTERNAL_ERROR`：内部异常。
    9. `INVALID_CONFIG`：配置非法。
    10. `DECODE_ERROR`：协议解码失败。
    """

    INVALID_MESSAGE = "INVALID_MESSAGE"
    UNAUTHORIZED = "UNAUTHORIZED"
    UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"
    DEVICE_BUSY = "DEVICE_BUSY"
    TASK_NOT_FOUND = "TASK_NOT_FOUND"
    STREAM_NOT_FOUND = "STREAM_NOT_FOUND"
    TIMEOUT = "TIMEOUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    INVALID_CONFIG = "INVALID_CONFIG"
    DECODE_ERROR = "DECODE_ERROR"


@dataclass(slots=True)
class AppError(Exception):
    """系统统一异常对象。

    主要功能：
    1. 把错误码、用户可读信息、是否可重试、上下文细节封装为同一结构。
    2. 在异常路径与正常响应路径之间复用同一错误数据。

    主要属性：
    1. `code`：错误码，来源于 `ErrorCode`。
    2. `message`：错误描述。
    3. `retryable`：是否建议调用方重试。
    4. `details`：附加细节。

    异常情况：
    1. 该类本身继承自 `Exception`，可被 `raise` 抛出。
    """

    code: ErrorCode
    message: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        """返回简短错误文本。

        返回值：
        1. 字符串，格式为 `错误码: 错误描述`。
        """

        return f"{self.code}: {self.message}"

    def to_dict(self) -> dict[str, Any]:
        """将错误对象转换为统一字典结构。

        返回值：
        1. 包含 `code/message/retryable/details` 的字典。
        """

        return {
            "code": str(self.code),
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }


def build_error(
    code: ErrorCode,
    message: str,
    *,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> AppError:
    """构造统一错误对象。

    主要逻辑：
    1. 对可选 `details` 做空值收敛。
    2. 返回可直接抛出或直接序列化的 `AppError`。

    参数：
    1. `code`：错误码。
    2. `message`：错误描述。
    3. `retryable`：是否可重试。
    4. `details`：可选附加信息。

    返回值：
    1. `AppError` 对象。
    """

    return AppError(
        code=code,
        message=message,
        retryable=retryable,
        details=details or {},
    )
