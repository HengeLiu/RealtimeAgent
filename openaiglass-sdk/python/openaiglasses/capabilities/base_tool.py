"""Tool 扩展基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from openaiglasses.models import CapabilityResult


class BaseTool(ABC):
    """短时能力基类。

    主要功能：
    1. 让开发者定义一次性执行的业务能力。
    2. 屏蔽设备连接、协议路由和上下文维护等系统问题。

    主要方法：
    1. `run`：执行工具逻辑。

    主要属性：
    1. `name`：工具名称。
    2. `description`：工具说明。
    """

    name: str = ""
    description: str = ""
    input_model: type[BaseModel] | None = None
    output_model: type[BaseModel] | None = None
    expose_to_model: bool = True

    @abstractmethod
    def run(self, context, input_data: dict[str, Any]) -> CapabilityResult:
        """执行 Tool。

        参数：
        1. `context`：SDK 提供的设备组上下文。
        2. `input_data`：调用方传入的业务参数。

        返回值：
        1. `CapabilityResult`：结构化执行结果。

        异常情况：
        1. 业务异常应尽量转成 `CapabilityResult.failed`。
        """
