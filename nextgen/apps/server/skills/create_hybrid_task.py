"""混合任务创建技能骨架实现。"""

from dataclasses import dataclass


@dataclass
class CreateHybridTaskSkill:
    """混合任务创建技能。

    主要功能：
    - 作为服务器侧创建混合任务的统一入口。

    当前阶段：
    - 只返回最小创建结果占位。
    """

    def run(self, task_name: str, params: dict) -> dict:
        """执行技能。

        参数：
        - task_name：任务名称
        - params：任务参数

        返回值：
        - 一个最小占位结果。
        """

        return {
            "task_name": task_name,
            "params": params,
            "status": "created_placeholder",
        }
