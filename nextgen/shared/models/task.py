"""任务模型定义。"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from nextgen.shared.enums.common import ExecutorMode, TaskCategory, TaskPriority, TaskStatus
from nextgen.shared.models.base import SourceTargetRef


@dataclass
class CapturePolicy:
    """任务默认采集策略。

    主要功能：
    - 描述某个任务模板默认依赖的采集行为。
    """

    sensor: str
    mode: str
    default_fps: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """将采集策略转换为字典。"""

        return {
            "sensor": self.sensor,
            "mode": self.mode,
            "default_fps": self.default_fps,
        }


@dataclass
class TaskDefinition:
    """任务模板定义。

    主要功能：
    - 描述一个任务模板的静态信息、依赖能力和默认策略。

    主要属性：
    - task_name：任务名称
    - task_category：任务类别
    - description：任务描述
    - executor_mode：执行模式
    - participants：参与运行时
    - input_schema：输入参数结构
    - phases：任务阶段集合
    - required_capabilities：不同运行时所需能力
    - capture_policies：默认采集策略
    - requires_peer_stream：是否需要眼镜与手机建立任务级长连接
    """

    task_name: str
    task_category: TaskCategory
    description: str
    executor_mode: ExecutorMode
    participants: List[str]
    input_schema: Dict[str, str] = field(default_factory=dict)
    phases: List[str] = field(default_factory=list)
    required_capabilities: Dict[str, List[str]] = field(default_factory=dict)
    capture_policies: List[CapturePolicy] = field(default_factory=list)
    requires_peer_stream: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """将任务模板转换为字典。"""

        return {
            "task_name": self.task_name,
            "task_category": self.task_category.value,
            "description": self.description,
            "executor_mode": self.executor_mode.value,
            "participants": self.participants,
            "input_schema": self.input_schema,
            "phases": self.phases,
            "required_capabilities": self.required_capabilities,
            "capture_policies": [item.to_dict() for item in self.capture_policies],
            "requires_peer_stream": self.requires_peer_stream,
        }


@dataclass
class TaskSession:
    """任务实例定义。

    主要功能：
    - 描述某个任务模板的一次真实运行。

    主要属性：
    - session_id：任务实例标识
    - task_name：任务名称
    - status：任务整体状态
    - phase：当前阶段
    - priority：优先级
    - created_at：创建时间
    - updated_at：更新时间
    - initiator：发起者
    - participants：参与设备
    - input：输入参数
    - last_state_summary：最近状态摘要
    - result：结果信息
    - error：错误信息
    - link_status：任务级连接状态
    """

    session_id: str
    task_name: str
    status: TaskStatus
    phase: str
    priority: TaskPriority
    created_at: str
    updated_at: str
    initiator: SourceTargetRef
    participants: Dict[str, List[str]] = field(default_factory=dict)
    input: Dict[str, Any] = field(default_factory=dict)
    last_state_summary: Dict[str, Any] = field(default_factory=dict)
    result: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    link_status: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """将任务实例转换为字典。"""

        return {
            "session_id": self.session_id,
            "task_name": self.task_name,
            "status": self.status.value,
            "phase": self.phase,
            "priority": self.priority.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "initiator": self.initiator.to_dict(),
            "participants": self.participants,
            "input": self.input,
            "last_state_summary": self.last_state_summary,
            "result": self.result,
            "error": self.error,
            "link_status": self.link_status,
        }
