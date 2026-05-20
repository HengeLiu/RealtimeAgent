"""Task Store 公开入口。

主要功能：为下一阶段 Task Engine 提供可替换的任务存储实现。
"""

from realtime_agent.tasks import JsonlTaskStore, TaskStore

__all__ = ["JsonlTaskStore", "TaskStore"]
