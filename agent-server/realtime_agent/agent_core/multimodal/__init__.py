"""Vision 多模态 message 管理兼容导入。

主要功能：保留历史 `realtime_agent.agent_core.multimodal` 导入路径；正式
conversation 多模态实现已迁移到 `realtime_agent.conversation.multimodal`。
"""

from realtime_agent.conversation.multimodal import MessageUpdate, ModelMessageManager, MultimodalMessagePolicy

__all__ = ["MessageUpdate", "ModelMessageManager", "MultimodalMessagePolicy"]
