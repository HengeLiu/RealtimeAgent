"""执行模型定义。"""

from dataclasses import dataclass, field
from typing import Any, Dict

from nextgen.shared.enums.common import ExecutionType, TaskPriority


@dataclass
class ExecutionPolicy:
    """执行策略。

    主要功能：
    - 约束执行请求是否可打断、是否在忙碌时丢弃。
    """

    interruptible: bool = True
    drop_if_busy: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """将执行策略转换为字典。"""

        return {
            "interruptible": self.interruptible,
            "drop_if_busy": self.drop_if_busy,
        }


@dataclass
class ExecutionRequest:
    """执行请求。

    主要功能：
    - 描述一次播报、提示音或震动等执行行为。
    """

    execution_id: str
    session_id: str
    execution_type: ExecutionType
    priority: TaskPriority
    payload: Dict[str, Any] = field(default_factory=dict)
    policy: ExecutionPolicy = field(default_factory=ExecutionPolicy)

    def to_dict(self) -> Dict[str, Any]:
        """将执行请求转换为字典。"""

        return {
            "execution_id": self.execution_id,
            "session_id": self.session_id,
            "type": self.execution_type.value,
            "priority": self.priority.value,
            "payload": self.payload,
            "policy": self.policy.to_dict(),
        }


@dataclass
class ExecutionFeedback:
    """执行反馈。

    主要功能：
    - 描述一次执行请求当前处于什么结果状态。
    """

    execution_id: str
    status: str
    timestamp: str

    def to_dict(self) -> Dict[str, Any]:
        """将执行反馈转换为字典。"""

        return {
            "execution_id": self.execution_id,
            "status": self.status,
            "timestamp": self.timestamp,
        }
