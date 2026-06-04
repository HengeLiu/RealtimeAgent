"""Vision conversation core 兼容导入。

主要功能：保留历史 `realtime_agent.agent_core.vision` 导入路径；正式音视频
conversation 链路的实现已迁移到 `realtime_agent.conversation.core.vision_host`。
"""

from realtime_agent.conversation.core.vision_host import *  # noqa: F401,F403
