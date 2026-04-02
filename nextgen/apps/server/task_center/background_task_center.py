"""后台任务中心实现。"""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from typing import Any, Dict, List, Optional

from nextgen.shared.contracts.task_center import TaskCenter
from nextgen.shared.enums.common import TaskPriority, TaskStatus
from nextgen.shared.models.base import SourceTargetRef
from nextgen.shared.models.task import TaskSession


class BackgroundTaskCenter(TaskCenter):
    """后台任务中心。

    主要功能：
    - 管理服务器侧后台任务实例
    - 提供创建、更新、状态流转和查询能力
    """

    def __init__(self) -> None:
        """初始化后台任务中心。"""

        self.sessions: Dict[str, TaskSession] = {}
        self.session_events: Dict[str, List[Dict[str, Any]]] = {}

    def create_session(self, session: TaskSession) -> TaskSession:
        """创建任务实例。"""

        self.sessions[session.session_id] = session
        self.session_events.setdefault(session.session_id, []).append(
            {
                "status": session.status.value,
                "phase": session.phase,
                "updated_at": session.updated_at,
                "summary": deepcopy(session.last_state_summary),
            }
        )
        return session

    def update_session(self, session: TaskSession) -> TaskSession:
        """更新任务实例。"""

        self.sessions[session.session_id] = session
        self.session_events.setdefault(session.session_id, []).append(
            {
                "status": session.status.value,
                "phase": session.phase,
                "updated_at": session.updated_at,
                "summary": deepcopy(session.last_state_summary),
            }
        )
        return session

    def finish_session(self, session_id: str) -> None:
        """结束任务实例。

        说明：
        - 第二阶段和第三阶段都希望保留任务实例，便于查询任务状态
        - 因此这里不删除实例，只把状态推进到 `completed`
        """

        session = self.sessions.get(session_id)
        if session is None:
            return
        self.update_session(
            replace(
                session,
                status=TaskStatus.COMPLETED,
                updated_at=datetime.now().astimezone().isoformat(),
            )
        )

    def create_runtime_session(
        self,
        session_id: str,
        task_name: str,
        initiator: SourceTargetRef,
        input_payload: Optional[Dict[str, Any]] = None,
        participants: Optional[Dict[str, List[str]]] = None,
        priority: TaskPriority = TaskPriority.HIGH,
    ) -> TaskSession:
        """创建一个面向运行时的任务实例。"""

        now = datetime.now().astimezone().isoformat()
        session = TaskSession(
            session_id=session_id,
            task_name=task_name,
            status=TaskStatus.CREATED,
            phase="created",
            priority=priority,
            created_at=now,
            updated_at=now,
            initiator=initiator,
            participants=participants or {},
            input=input_payload or {},
        )
        return self.create_session(session)

    def transition_session(
        self,
        session_id: str,
        status: TaskStatus,
        phase: str,
        summary: Optional[Dict[str, Any]] = None,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[Dict[str, Any]] = None,
    ) -> TaskSession:
        """推进任务状态。"""

        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError(f"任务实例不存在: {session_id}")

        updated = replace(
            session,
            status=status,
            phase=phase,
            updated_at=datetime.now().astimezone().isoformat(),
            last_state_summary=summary or session.last_state_summary,
            result=result if result is not None else session.result,
            error=error if error is not None else session.error,
        )
        return self.update_session(updated)

    def get_session(self, session_id: str) -> Optional[TaskSession]:
        """获取单个任务实例。"""

        session = self.sessions.get(session_id)
        return deepcopy(session) if session else None

    def list_sessions(self) -> List[TaskSession]:
        """列出所有任务实例。"""

        return [deepcopy(session) for session in self.sessions.values()]

    def get_session_events(self, session_id: str) -> List[Dict[str, Any]]:
        """获取某个任务实例的状态事件列表。"""

        return deepcopy(self.session_events.get(session_id, []))
