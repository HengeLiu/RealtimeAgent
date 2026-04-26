"""红绿灯识别 Tool。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from openaiglasses import BaseTool, CapabilityResult


class StartTrafficLightInput(BaseModel):
    """红绿灯识别 Tool 输入。"""

    crossing_name: str = Field(default="", description="路口或过街位置名称，可为空")
    stop_after_first_signal: bool = Field(default=True, description="识别到第一个有效信号后是否结束任务")


class StartTrafficLightOutput(BaseModel):
    """红绿灯识别 Tool 输出。"""

    task_id: str
    task_type: str
    state: str
    crossing_name: str
    stop_after_first_signal: bool
    task_data: dict[str, Any]


class StartTrafficLightTool(BaseTool):
    """启动红绿灯识别任务的工具。

    主要功能：
    1. 接收用户希望识别的路口名称。
    2. 通过 SDK 创建红绿灯识别后台任务。
    3. 返回任务编号和初始状态。

    主要方法：
    1. `run`：执行任务创建逻辑。
    """

    name = "start_traffic_light_detection"
    description = "启动红绿灯识别任务，用于辅助用户判断当前是否可以过街"
    input_model = StartTrafficLightInput
    output_model = StartTrafficLightOutput

    def run(self, context, input_data: dict[str, Any]) -> CapabilityResult:
        """启动红绿灯识别任务。

        参数：
        1. `context`：SDK 提供的设备组上下文。
        2. `input_data`：业务输入，包含路口名称和结束策略。

        返回值：
        1. `CapabilityResult`：任务创建结果。

        异常情况：
        1. SDK 任务运行时未装配时由 SDK 统一抛出异常。
        """

        crossing_name = str(input_data.get("crossing_name") or "").strip()
        stop_after_first_signal = bool(input_data.get("stop_after_first_signal", True))
        task_runtime = context.create_task(
            task_type="traffic_light_task",
            input_data={
                "crossing_name": crossing_name,
                "stop_after_first_signal": stop_after_first_signal,
            },
        )
        return CapabilityResult.success(
            data={
                "task_id": task_runtime.task_id,
                "task_type": task_runtime.task_type,
                "state": task_runtime.state,
                "crossing_name": crossing_name,
                "stop_after_first_signal": stop_after_first_signal,
                "task_data": task_runtime.data,
            },
            message="已启动红绿灯识别任务",
        )

