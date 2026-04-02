"""本地任务中心骨架实现。"""

from nextgen.shared.contracts.task_center import TaskCenter
from nextgen.shared.models.task import TaskSession


class LocalTaskCenter(TaskCenter):
    """手机端本地任务中心。

    主要功能：
    - 管理手机侧本地任务实例。
    """

    def __init__(self) -> None:
        """初始化本地任务中心。"""

        self.sessions: dict[str, TaskSession] = {}

    def create_session(self, session: TaskSession) -> TaskSession:
        """创建任务实例。"""

        self.sessions[session.session_id] = session
        return session

    def update_session(self, session: TaskSession) -> TaskSession:
        """更新任务实例。"""

        self.sessions[session.session_id] = session
        return session

    def finish_session(self, session_id: str) -> None:
        """结束任务实例。"""

        self.sessions.pop(session_id, None)
