"""模型 provider adapter 兼容导入。

主要功能：保留历史 `realtime_agent.agent_core.providers` 导入路径；正式
conversation 链路的 provider adapter 实现已迁移到
`realtime_agent.conversation.providers.model_adapters`。
"""

from realtime_agent.conversation.providers.model_adapters import *  # noqa: F401,F403
