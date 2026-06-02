from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from realtime_agent.conversation.types import AgentOutputDelta, SpeechInputDelta


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
    metadata: Mapping[str, Any] = field(default_factory=dict)


class ConversationAgentCore(Protocol):
    """conversation Agent Core 抽象接口。

    主要功能：消费 `SpeechInputDelta`，管理链路专属 provider 和 turn 行为，并把
    结果交给输出适配层。
    """

    async def open(self, context: ConversationContext) -> None:
        """打开一个 conversation 会话。"""

    async def consume_speech(self, delta: SpeechInputDelta) -> None:
        """消费语音输入增量。"""

    async def interrupt(self, reason: str = "user_speech") -> None:
        """请求中断当前输出或生成。"""

    async def close(self) -> None:
        """关闭当前会话并释放链路资源。"""


class ConversationOutputAdapter(Protocol):
    """conversation 输出适配层抽象接口。

    主要功能：把 Agent Core 输出的 `AgentOutputDelta` 转交给现有 OutputService，
    保持新旧链路共用播放仲裁。
    """

    async def emit(self, delta: AgentOutputDelta) -> None:
        """发送一个输出增量。"""

    async def cancel_current(self, reason: str) -> None:
        """取消当前输出。"""

    async def close(self) -> None:
        """关闭输出适配层。"""
