"""conversation 上下文管理入口。

主要功能：
1. 暴露 PromptRegistry、ContextCompiler 和 ModelContext 等轻量对象。
2. 让 VL / Omni conversation core 通过同一套接口生成模型可见上下文。
3. 保持第一版实现简单，不绑定外部 prompt 管理平台。
"""

from realtime_agent.conversation.context.compiler import ContextCompileRequest, ContextCompiler, record_context_events
from realtime_agent.conversation.context.models import ContextSource, ModelContext, PromptAsset, normalize_history_message, normalize_tool_call
from realtime_agent.conversation.context.policy import ContextPolicy
from realtime_agent.conversation.context.registry import PromptRegistry

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
