from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from realtime_agent.protocol import StreamChunk


@dataclass(frozen=True)
class AgentCoreEvent:
    """Agent Core 统一事件。

    主要功能：
    1. 给 `VisionRealtimeAgentCore`、`OmniRealtimeAgentCore` 和自定义 core 提供统一事件快照。
    2. 让测试、预检和运行产物可以按相同字段理解 Agent 会话状态。

    主要属性：
    1. `event`：事件名称，例如 `session.opened`、`response.done`。
    2. `user_id`：事件关联用户，可为空。
    3. `session_id`：事件关联会话，可为空。
    4. `payload`：事件补充字段。
    """

    event: str
    user_id: str = ""
    session_id: str = ""
    payload: dict = field(default_factory=dict)


class AgentCore(Protocol):
    """Agent Core 公共接口。

    主要功能：
    1. 统一Vision 链路、实时音频链路和自定义 Agent Core 的生命周期。
    2. 避免 Audio Pipeline、App 和测试依赖某个具体 core 的私有方法。

    主要方法：
    1. `open`：打开一个用户会话。
    2. `append_audio_event`：追加归一后的 `sensor.mic` chunk。
    3. `commit_input`：提交当前输入边界。
    4. `interrupt`：处理用户打断。
    5. `close`：关闭用户会话并释放 provider。
    6. `events`：返回统一事件快照。
    """

    def open(self, user_id: str, session_id: str) -> None:
        """打开用户会话。"""

    def append_audio_event(self, chunk: StreamChunk) -> None:
        """追加归一后的音频事件。"""

    def commit_input(self, user_id: str, session_id: str, *, reason: str = "endpoint_commit") -> None:
        """提交当前输入边界。"""

    def interrupt(self, user_id: str, *, reason: str) -> None:
        """取消当前响应。"""

    def close(self, user_id: str, *, reason: str) -> None:
        """关闭用户会话。"""

    def events(self) -> list[AgentCoreEvent]:
        """返回事件快照。"""


class AgentEventBuffer:
    """Agent Core 事件缓存。

    主要功能：
    1. 保存最近产生的统一 Agent 事件。
    2. 以简单列表形式支持单元测试和 debug snapshot。

    主要方法：
    1. `record_event`：追加事件。
    2. `events`：返回事件快照。
    """

    def __init__(self) -> None:
        self._events: list[AgentCoreEvent] = []

    def record_event(
        self,
        event: str,
        *,
        user_id: str = "",
        session_id: str = "",
        payload: dict | None = None,
    ) -> AgentCoreEvent:
        """记录一个统一 Agent 事件。

        参数：
        1. `event`：事件名称。
        2. `user_id`：用户编号。
        3. `session_id`：会话编号。
        4. `payload`：补充字段。

        返回值：
        1. `AgentCoreEvent`：刚写入的事件。

        异常情况：
        1. 本函数不抛出业务异常。
        """

        item = AgentCoreEvent(event=event, user_id=user_id, session_id=session_id, payload=dict(payload or {}))
        self._events.append(item)
        return item

    def events(self) -> list[AgentCoreEvent]:
        """返回 Agent 事件快照。"""

        return list(self._events)
