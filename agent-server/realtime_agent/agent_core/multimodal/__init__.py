"""Vision 多模态 message 管理模块。

主要功能：把工具返回的图片 / 视频资产转换成 Vision provider 可消费的
content blocks，并记录可复查的 source map。
"""

from realtime_agent.agent_core.multimodal.messages import MessageUpdate, ModelMessageManager
from realtime_agent.agent_core.multimodal.policy import MultimodalMessagePolicy

__all__ = ["MessageUpdate", "ModelMessageManager", "MultimodalMessagePolicy"]
