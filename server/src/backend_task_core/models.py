"""backend-task-core 对象模型。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


def now_ms() -> int:
    """返回当前毫秒时间戳。"""

    return int(time.time() * 1000)


@dataclass(slots=True)
class TaskSpec:
    """后台任务模板定义。

    主要功能：
    1. 描述某类任务的固定元信息。
    2. 为注册表、状态机和调度器提供统一模板输入。

    主要属性：
    1. `task_type`：任务类型，例如 `timer_task`。
    2. `version`：任务模板版本。
    3. `description`：任务说明。
    4. `supports_cancel`：是否允许取消。
    5. `timeout_seconds`：任务超时秒数。
    """

    task_type: str
    version: str
    description: str
    supports_cancel: bool = True
    supports_pause: bool = False
    supports_resume: bool = False
    timeout_seconds: int = 86400


@dataclass(slots=True)
class TaskRuntime:
    """后台任务实例模型。

    主要功能：
    1. 保存某个任务实例的生命周期状态。
    2. 作为查询、取消、事件发布和联调观察的统一载体。

    主要属性：
    1. `task_id`：任务实例编号。
    2. `task_type`：任务类型。
    3. `session_id/device_id`：任务所属会话与设备。
    4. `state`：统一生命周期状态。
    5. `input/context/result/error`：任务输入、上下文、结果与错误。
    """

    task_id: str
    task_type: str
    version: str
    session_id: str
    device_id: str
    state: str
    input: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    created_at_ms: int = field(default_factory=now_ms)
    updated_at_ms: int = field(default_factory=now_ms)
    started_at_ms: int | None = None
    completed_at_ms: int | None = None
    parent_task_id: str | None = None


@dataclass(slots=True)
class TaskEvent:
    """后台任务事件。

    主要功能：
    1. 记录任务生命周期中的结构化事件。
    2. 为任务事件回流、通知和调试提供统一对象。

    主要属性：
    1. `event_name`：事件名，例如 `task.completed`。
    2. `task_id/task_type`：事件所属任务。
    3. `state`：事件发生时的任务状态。
    4. `priority`：事件优先级。
    5. `requires_agent_decision`：是否需要回流给对话层继续决策。
    6. `allow_direct_notify`：是否允许直接通知设备。
    """

    event_id: str
    event_name: str
    task_id: str
    task_type: str
    session_id: str
    device_id: str
    state: str
    priority: str
    requires_agent_decision: bool
    allow_direct_notify: bool
    ts: int
    payload: dict[str, Any] = field(default_factory=dict)
