"""历史 Agent Core 抽象兼容导出。

正式音视频 conversation 链路的 Agent Core 抽象位于
`realtime_agent.conversation.core.base`。本模块只保留旧导入路径，避免外部测试或
应用代码在迁移期立即失效。
"""

from realtime_agent.conversation.core.base import AgentCore, AgentCoreEvent, AgentEventBuffer

__all__ = [
    "AgentCore",
    "AgentCoreEvent",
    "AgentEventBuffer",
]
