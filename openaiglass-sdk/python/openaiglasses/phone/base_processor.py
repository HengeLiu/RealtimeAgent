"""手机处理器扩展基类。"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class PhoneProcessorContext:
    """手机处理器上下文。

    主要功能：
    1. 向手机侧处理器提供任务参数。
    2. 收集处理器输出的结构化结果。

    主要属性：
    1. `params`：处理器启动参数。
    2. `results`：处理器输出结果列表。
    """

    params: dict[str, Any] = field(default_factory=dict)
    results: list[dict[str, Any]] = field(default_factory=list)

    def emit_result(self, result: dict[str, Any]) -> None:
        """输出处理器结果。

        参数：
        1. `result`：结构化处理结果。

        返回值：
        1. 无。

        异常情况：
        1. 本函数不主动抛出异常。
        """

        self.results.append(result)


class BasePhoneProcessor(ABC):
    """手机侧帧处理器基类。

    主要功能：
    1. 让开发者实现手机本地视觉、传感器或轻量推理能力。
    2. 屏蔽手机注册、媒体接收和结果回流等系统问题。

    主要方法：
    1. `on_frame`：处理单帧或一段输入。
    """

    processor_type: str = ""
    description: str = ""

    def on_frame(self, context: PhoneProcessorContext, frame: Any) -> None:
        """处理输入帧。

        参数：
        1. `context`：手机处理器上下文。
        2. `frame`：SDK 或端侧适配层传入的帧对象。

        返回值：
        1. 无。

        异常情况：
        1. 子类可以按业务需要抛出异常，由 SDK 或端侧适配层统一记录。
        """
