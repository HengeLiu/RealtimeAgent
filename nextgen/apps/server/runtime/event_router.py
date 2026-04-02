"""事件任务分发骨架实现。"""

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class EventRouter:
    """事件任务分发器。

    主要功能：
    - 接收来自接入层的新事件
    - 根据配置决定是否启用关键词快速分发
    - 为后续智能体或后台任务中心提供统一入口

    主要属性：
    - enable_keyword_dispatch：是否启用关键词检测任务分发
    """

    enable_keyword_dispatch: bool = False

    def route(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """路由一个事件。

        主要逻辑：
        - 当前阶段不实现真实意图判断，只返回最小路由结果。
        - 若开启关键词分发，则在结果中标出当前已启用。

        参数：
        - event：待路由事件对象。

        返回值：
        - 最小路由结果。
        """

        return {
            "received": True,
            "keyword_dispatch_enabled": self.enable_keyword_dispatch,
            "event": event,
        }
