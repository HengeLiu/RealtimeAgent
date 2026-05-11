from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    """SDK 稳定错误码。

    主要功能：为 Tool、Task、服务启动和协议处理提供可机器读取的错误分类。
    主要属性：枚举值使用小写字符串，便于写入 JSON 产物。
    """

    UNKNOWN = "unknown"
    INVALID_ARGUMENT = "invalid_argument"
    NOT_FOUND = "not_found"
    PERMISSION_DENIED = "permission_denied"
    TIMEOUT = "timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROTOCOL_ERROR = "protocol_error"


class AudioChatError(Exception):
    """audio-chat SDK 基础异常。

    主要功能：携带稳定 `ErrorCode` 和可选详情，供 Tool / Task / Gateway 统一返回。
    主要方法：`to_dict()` 将异常转换为可记录到 runs 的结构化对象。
    """

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode = ErrorCode.UNKNOWN,
        retryable: bool = False,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.details = dict(details or {})

    def to_dict(self) -> dict:
        """转换为结构化错误。

        主要逻辑：保留错误码、消息和详情，避免调用方解析异常字符串。
        参数：无。
        返回值：可 JSON 序列化的字典。
        异常情况：无。
        """
        return {
            "code": self.code.value,
            "message": str(self),
            "retryable": self.retryable,
            "details": self.details,
        }
