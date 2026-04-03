"""本地任务中心实现。"""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from typing import Any, Dict, List, Optional

from nextgen.shared.contracts.task_center import TaskCenter
from nextgen.shared.enums.common import TaskStatus
from nextgen.shared.models.task import TaskSession


class LocalTaskCenter(TaskCenter):
    """手机端本地任务中心。

    主要功能：
    - 管理手机侧本地任务实例
    - 提供状态推进与查询能力
    """

    def __init__(self) -> None:
        """初始化本地任务中心。"""

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
            }
        )
        return session

    def finish_session(self, session_id: str) -> None:
        """结束任务实例。"""

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

    def get_session(self, session_id: str) -> Optional[TaskSession]:
        """获取单个任务实例。"""

        session = self.sessions.get(session_id)
        return deepcopy(session) if session else None

    def list_sessions(self) -> List[TaskSession]:
        """列出全部任务实例。"""

        return [deepcopy(session) for session in self.sessions.values()]

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
