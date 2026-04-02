"""任务中心抽象接口。"""

from abc import ABC, abstractmethod

from nextgen.shared.models.task import TaskSession


class TaskCenter(ABC):
    """任务中心抽象接口。

    主要功能：
    - 创建、更新、结束任务实例。
    """

    @abstractmethod
    def create_session(self, session: TaskSession) -> TaskSession:
        """创建任务实例。"""

    @abstractmethod
    def update_session(self, session: TaskSession) -> TaskSession:
        """更新任务实例。"""

    @abstractmethod
    def finish_session(self, session_id: str) -> None:
        """结束任务实例。"""
