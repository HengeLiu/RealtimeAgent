"""混合任务创建技能实现。"""

from dataclasses import dataclass
from typing import Any, Dict, Optional
from uuid import uuid4

from nextgen.apps.server.task_center.background_task_center import BackgroundTaskCenter
from nextgen.apps.server.storage.state_log_store import StateLogStore
from nextgen.shared.enums.common import RuntimeType, TaskPriority, TaskStatus
from nextgen.shared.models.base import SourceTargetRef


@dataclass
class CreateHybridTaskSkill:
    """混合任务创建技能。

    主要功能：
    - 作为服务器侧创建混合任务的统一入口
    - 创建任务实例
    - 初始化任务参与方和首个状态事件
    """

    task_center: Optional[BackgroundTaskCenter] = None
    state_log_store: Optional[StateLogStore] = None

    def run(
        self,
        task_name: str,
        params: Dict[str, Any],
        initiator: Optional[SourceTargetRef] = None,
        priority: TaskPriority = TaskPriority.HIGH,
    ) -> Dict[str, Any]:
        """执行技能。

        参数：
        - task_name：任务名称
        - params：任务参数
        - initiator：任务发起方
        - priority：任务优先级

        返回值：
        - 标准化的任务创建结果。
        """

        session_id = f"tasksess_{uuid4().hex[:12]}"
        initiator = initiator or SourceTargetRef(runtime=RuntimeType.SERVER, device_id="server-main")

        result = {
            "session_id": session_id,
            "task_name": task_name,
            "params": params,
            "status": TaskStatus.CREATED.value,
            "participants": {
                "glass": ["capture", "executor"],
                "phone": ["local_task"],
                "server": ["task_center", "agent"],
            },
        }

        if self.task_center is not None:
            session = self.task_center.create_runtime_session(
                session_id=session_id,
                task_name=task_name,
                initiator=initiator,
                input_payload=params,
                participants=result["participants"],
                priority=priority,
            )
            session = self.task_center.transition_session(
                session_id=session.session_id,
                status=TaskStatus.STARTING,
                phase="dispatching",
                summary={"task_name": task_name, "params": params},
            )
            result["status"] = session.status.value
            result["phase"] = session.phase

        if self.state_log_store is not None:
            self.state_log_store.append_task_event(
                session_id=session_id,
                event={
                    "event_name": "hybrid_task_created",
                    "task_name": task_name,
                    "params": params,
                },
            )

        return result
