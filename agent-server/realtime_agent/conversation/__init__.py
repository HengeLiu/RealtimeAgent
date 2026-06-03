"""conversation 运行时入口。

主要功能：保留旧会话记忆服务的公开导入，同时导出新音视频 conversation
runtime 的基础类型和抽象接口。这样外部仍可使用
`from realtime_agent.conversation import ConversationMemoryService`，后续新链路也能在
同一包名下继续演进。
"""

from realtime_agent.conversation.core.base import (
    AgentCoreABC,
    AgentLoopABC,
    AgentMemoryABC,
    AgentSnapshot,
    ConversationAgentCore,
    ConversationContext,
    ConversationOutputAdapter,
    TaskSignal,
)
from realtime_agent.conversation.config import ConversationRuntimeConfig
from realtime_agent.conversation.turn import OutputInterruptionController, RealtimeTurnController
from realtime_agent.conversation.memory import (
    ConversationMemoryService,
    ConversationSummaryError,
    LlmMessageSummarizer,
    MessageSummary,
)
from realtime_agent.conversation.types import AgentOutputDelta, SpeechInputDelta

__all__ = [
    "AgentOutputDelta",
    "AgentCoreABC",
    "AgentLoopABC",
    "AgentMemoryABC",
    "AgentSnapshot",
    "ConversationAgentCore",
    "ConversationContext",
    "ConversationMemoryService",
    "ConversationRuntimeConfig",
    "ConversationOutputAdapter",
    "ConversationSummaryError",
    "LlmMessageSummarizer",
    "MessageSummary",
    "OutputInterruptionController",
    "RealtimeTurnController",
    "SpeechInputDelta",
    "TaskSignal",
]
