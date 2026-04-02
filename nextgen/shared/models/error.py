"""错误模型定义。"""

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class ErrorInfo:
    """标准错误定义。

    主要功能：
    - 统一描述错误码、错误信息、是否可重试和扩展上下文。
    """

    code: str
    message: str
    retryable: bool
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """将错误信息转换为字典。"""

        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "detail": self.detail,
        }
