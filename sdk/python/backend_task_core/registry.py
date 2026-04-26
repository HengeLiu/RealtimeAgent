"""后台任务注册表。"""

from __future__ import annotations

from backend_task_core.models import TaskSpec
from infra.errors import ErrorCode, build_error


class TaskRegistry:
    """任务模板注册表。

    主要功能：
    1. 注册系统支持的任务模板。
    2. 根据 `task_type` 返回模板定义。
    """

    def __init__(self) -> None:
        self._specs: dict[str, TaskSpec] = {}

    def get_spec(self, task_type: str) -> TaskSpec:
        """读取任务模板定义。"""

        spec = self._specs.get(task_type)
        if spec is None:
            raise build_error(
                ErrorCode.TASK_NOT_FOUND,
                "未找到对应任务模板",
                details={"task_type": task_type},
            )
        return spec
