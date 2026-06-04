"""模型视觉 append 兼容导入。

主要功能：保留历史 `realtime_agent.agent_core.visual.appender` 导入路径；正式
conversation 链路的视觉 append 适配已迁移到
`realtime_agent.conversation.input.visual_appender`。
"""

from realtime_agent.conversation.input.visual_appender import *  # noqa: F401,F403
