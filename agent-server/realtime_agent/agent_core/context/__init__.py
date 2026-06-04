"""Agent Core 上下文管理兼容导入。

主要功能：保留历史 `realtime_agent.agent_core.context` 导入路径；正式
conversation 上下文实现已迁移到 `realtime_agent.conversation.context`。
"""

from realtime_agent.conversation.context import (
    ContextCompileRequest,
    ContextCompiler,
    ContextPolicy,
    ContextSource,
    ModelContext,
    PromptAsset,
    PromptRegistry,
    record_context_events,
)

__all__ = [
    "ContextCompileRequest",
    "ContextCompiler",
    "ContextPolicy",
    "ContextSource",
    "ModelContext",
    "PromptAsset",
    "PromptRegistry",
    "record_context_events",
]
