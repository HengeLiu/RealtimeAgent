"""计时器 Tool。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from openaiglasses import BaseTool, CapabilityResult


class StartTimerInput(BaseModel):
    """计时器 Tool 输入。"""

    duration_seconds: int = Field(description="用户要求计时的时长，必须换算成秒，例如 5 分钟填写 300。")
    label: str = Field(default="计时器", description="给这个计时器起的名称；用户没有命名时使用默认值“计时器”。")
    notify_text: str = Field(default="", description="计时结束时要播报给用户的话；用户没有指定时可以留空。")
    enable_background_timer: bool = Field(
        default=True,
        description="是否在服务端启动真实倒计时；正常语音使用保持 true，单元测试可设为 false。",
    )


class StartTimerOutput(BaseModel):
    """计时器 Tool 输出。"""

    task_id: str
    task_type: str
    state: str
    duration_seconds: int
    label: str
    task_data: dict[str, Any]


class StartTimerTool(BaseTool):
    """启动计时器任务的工具。

    主要功能：
    1. 接收计时时长和计时器名称。
    2. 通过 SDK 创建 `timer_task`。
    3. 返回结构化任务信息。

    主要方法：
    1. `run`：校验输入并创建计时器任务。
    """

    name = "start_timer"
    description = "当用户要求倒计时、计时或到时间提醒时调用；不要用于普通日程或长期提醒。"
    input_model = StartTimerInput
    output_model = StartTimerOutput

    def run(self, context, input_data: dict[str, Any]) -> CapabilityResult:
        """启动计时器。

        参数：
        1. `context`：SDK 设备组上下文。
        2. `input_data`：业务输入，包含计时时长、名称和结束提示。

        返回值：
        1. `CapabilityResult`：任务创建结果。

        异常情况：
        1. 时长小于等于 0 时返回结构化失败结果。
        """

        duration_seconds = int(input_data.get("duration_seconds") or 0)
        if duration_seconds <= 0:
            return CapabilityResult.failed(
                code="invalid_input",
                message="duration_seconds 必须大于 0",
                details={"duration_seconds": duration_seconds},
            )
        label = str(input_data.get("label") or "计时器").strip() or "计时器"
        notify_text = str(input_data.get("notify_text") or "").strip()
        task = context.create_task(
            task_type="timer_task",
            input_data={
                "duration_seconds": duration_seconds,
                "label": label,
                "notify_text": notify_text,
                "enable_background_timer": bool(input_data.get("enable_background_timer", True)),
            },
        )
        return CapabilityResult.success(
            data={
                "task_id": task.task_id,
                "task_type": task.task_type,
                "state": task.state,
                "duration_seconds": duration_seconds,
                "label": label,
                "task_data": task.data,
            },
            message=f"已启动{label}，时长 {duration_seconds} 秒",
        )
