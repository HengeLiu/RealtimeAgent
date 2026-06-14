from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from realtime_agent.protocol import StreamChunk
from realtime_agent.conversation.types import AgentOutputDelta, SpeechInputDelta


@dataclass(frozen=True)
class AgentCoreEvent:
    """Agent Core 统一事件。

    主要功能：
    1. 给 Omni、VL 和自定义 Agent Core 提供统一事件快照。
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
    """旧 App 接入层使用的 Agent Core 兼容接口。

    主要功能：在 `RealtimeAgentApp` 仍通过旧方法名调用 core 的阶段，描述
    conversation runtime 对外提供的最小兼容面。新设计层面的抽象以
    `AgentCoreABC` 为准。
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

        返回值：刚写入的 `AgentCoreEvent`。
        异常情况：本函数不抛出业务异常。
        """

        item = AgentCoreEvent(event=event, user_id=user_id, session_id=session_id, payload=dict(payload or {}))
        self._events.append(item)
        return item

    def events(self) -> list[AgentCoreEvent]:
        """返回 Agent 事件快照。"""

        return list(self._events)


@dataclass(frozen=True, slots=True)
class ConversationContext:
    """conversation runtime 的会话上下文。

    主要功能：保存 Agent Core 打开时所需的用户、会话、运行时和配置标识。
    主要属性：`user_id/session_id` 定位当前会话；`mode` 区分 omni 与 vision；
    `metadata` 保存后续阶段需要透传但暂未稳定成字段的上下文。
    """

    user_id: str
    session_id: str
    mode: str
    runtime: str = "conversation"
    device_id: str | None = None
    active_streams: Mapping[str, str] = field(default_factory=dict)
    system_prompt: str | None = None
    current_turn: Mapping[str, Any] = field(default_factory=dict)
    tool_schemas: tuple[Mapping[str, Any], ...] = ()
    memory_summary: Any | None = None
    recorder: Any | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentSnapshot:
    """AgentCore 状态快照。

    主要功能：给调试、preflight 和后续 orchestration 提供只读状态视图。
    """

    user_id: str | None = None
    session_id: str | None = None
    mode: str | None = None
    state: str | None = None
    active_streams: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)


class AgentCoreABC(Protocol):
    """Agent Core 抽象接口。

    主要功能：消费 `SpeechInputDelta`，管理链路专属 provider 和 turn 行为，并把
    结果交给输出适配层。
    """

    def open(self, context: ConversationContext) -> None:
        """按 AgentContext 打开一个 conversation 会话。"""

    def open_context(self, context: ConversationContext) -> None:
        """打开一个 conversation 会话。"""

    def consume_input(self, delta: SpeechInputDelta) -> None:
        """消费标准输入增量。"""

    def interrupt(self, user_id: str, *, reason: str = "user_speech") -> None:
        """请求中断当前输出或生成。"""

    def close(self, user_id: str, *, reason: str) -> None:
        """关闭当前会话并释放链路资源。"""

    def snapshot(self) -> AgentSnapshot:
        """返回当前 AgentCore 只读状态快照。"""


class AgentLoopABC(Protocol):
    """Agent Loop 抽象接口。

    主要功能：封装一次或多次 provider 调用、tool call 回填和输出 delta 生成。
    AgentLoop 不持有设备连接，也不直接管理 speaker stream。
    """

    def run(self, context: ConversationContext) -> None:
        """执行一次 Agent 响应循环。"""

    def consume_input(self, delta: SpeechInputDelta) -> None:
        """消费标准输入增量并推进当前 loop。"""

    def interrupt(self, reason: str) -> None:
        """中断当前响应循环。"""


class AgentMemoryABC(Protocol):
    """Agent Memory 抽象接口。

    主要功能：保存和读取 Agent 可见的对话消息与压缩摘要。现有
    `ConversationMemoryService` 通过同名方法结构化实现该接口。
    """

    def append_message(self, *, user_id: str, device_id: str, message: dict[str, Any]) -> None:
        """追加一条会话消息。"""

    def load_active_messages(self, *, user_id: str, device_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """读取当前模型可见消息。"""

    def load_latest_summary(self, *, user_id: str, device_id: str) -> Any:
        """读取最近一次压缩摘要。"""


class ConversationAgentCore(AgentCoreABC, Protocol):
    """conversation Agent Core 兼容别名。

    主要功能：保留旧导入名，同时向设计文档中的 `AgentCoreABC` 收敛。
    """


class ConversationOutputAdapter(Protocol):
    """conversation 输出适配层抽象接口。

    主要功能：把 Agent Core 输出的 `AgentOutputDelta` 转交给现有 OutputService，
    保持新旧链路共用播放仲裁。
    """

    def emit(self, delta: AgentOutputDelta) -> None:
        """发送一个输出增量。"""

    def cancel_current(self, *, user_id: str, session_id: str, reason: str) -> None:
        """取消当前输出。"""

    def close(self) -> None:
        """关闭输出适配层。"""
