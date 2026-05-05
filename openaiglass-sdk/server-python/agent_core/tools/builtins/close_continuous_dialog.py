"""关闭连续对话 Tool。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from agent_core.models import CapabilityResult, ToolSpec
from agent_core.tools.base import AgentToolContext, BaseTool


class CloseContinuousDialogInput(BaseModel):
    """关闭连续对话输入。"""

    mode: Literal["after_reply"] = Field(
        default="after_reply",
        description="关闭方式。当前 SDK 固定为 after_reply，表示等当前回复播报完成后关闭连续对话。",
    )


class CloseContinuousDialogOutput(BaseModel):
    """关闭连续对话输出。"""

    scheduled: bool
    mode: str


class CloseContinuousDialogTool(BaseTool):
    """让模型声明本轮回复后应关闭端侧连续对话窗口。"""

    spec = ToolSpec(
        name="close_continuous_dialog",
        description=(
            "只能在用户明确表达结束连续对话、希望助手安静、先不用继续听、先这样、等会儿再说等意图时调用。"
            "不要因为一次普通问题已经回答完成就调用，也不要用它表示本轮回复结束。"
            "调用后运行时会在当前回复播报完成后关闭连续对话窗口，让眼镜回到需要唤醒词的待命状态。"
        ),
        input_model=CloseContinuousDialogInput,
        output_model=CloseContinuousDialogOutput,
        capability_type="tool",
        tags=["voice", "dialog", "system"],
        progress_message=None,
    )

    def run(self, context: AgentToolContext, input_data: CloseContinuousDialogInput) -> CapabilityResult:
        """记录连续对话关闭意图。

        主要逻辑：
        1. 不直接操作设备连接，避免 Tool 层依赖 voice-runtime 内部对象。
        2. 把关闭意图写入本轮 `turn_meta`，由 voice-runtime 在回复播报完成后执行。
        3. 返回结构化结果，让模型知道本轮关闭请求已经被 SDK 接收。

        参数：
            context: 当前 Agent 工具上下文。
            input_data: 模型声明的关闭方式。

        返回值：
            `CapabilityResult`，其中 `scheduled=True` 表示关闭意图已记录。

        异常情况：
            本工具不抛出业务异常。
        """

        request = {
            "scheduled": True,
            "mode": input_data.mode,
            "source": "model_tool",
            "tool_name": self.spec.name,
        }
        context.turn_meta["close_continuous_dialog"] = request
        return CapabilityResult.success(
            data={
                "scheduled": True,
                "mode": input_data.mode,
            },
            message="已安排在当前回复结束后关闭连续对话。",
        )
