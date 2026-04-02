"""共享基础模型。

本文件定义标识、时间、引用等多个模型都会复用的基础对象。
"""

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

from nextgen.shared.enums.common import RuntimeType


@dataclass
class SourceTargetRef:
    """消息源和目标的基础引用。

    主要功能：
    - 描述一条消息或一个消费者属于哪个运行时、哪个设备。

    主要属性：
    - runtime：运行时类型
    - device_id：设备唯一标识
    - component：可选的组件名称，用于更细粒度路由
    """

    runtime: RuntimeType
    device_id: str
    component: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """将对象转换为字典。

        返回值：
        - 适合序列化的字典对象。
        """

        return asdict(self)


@dataclass
class Resolution:
    """分辨率定义。

    主要功能：
    - 用统一结构描述图像或视频的宽高信息。
    """

    width: int
    height: int

    def to_dict(self) -> Dict[str, Any]:
        """将对象转换为字典。"""

        return asdict(self)
