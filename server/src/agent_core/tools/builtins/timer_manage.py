"""计时器管理 Tool。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from agent_core.models import CapabilityResult, ToolSpec
from agent_core.tools.base import AgentToolContext, BaseTool
from infra.errors import ErrorCode, build_error


class TimerManageInput(BaseModel):
    """计时器管理输入。

    主要功能：
    1. 统一承接创建、查询、取消计时器三类用户意图。
    2. 允许模型只给出自然语言原句，由 Tool 在内部推断具体动作。

    主要属性：
    1. `action`：显式指定操作类型，默认自动判断。
    2. `duration_seconds`：创建计时器时需要的时长。
    3. `task_id`：查询或取消时使用的任务编号。
    4. `label`：计时器展示标签。
    5. `query`：原始用户意图文本，用于自动解析。
    """

    action: Literal["auto", "create", "query", "cancel"] = Field(default="auto", description="管理动作")
    duration_seconds: int | None = Field(default=None, description="创建时需要的倒计时秒数")
    task_id: str | None = Field(default=None, description="查询或取消时的任务编号")
    label: str | None = Field(default=None, description="任务标签")
    query: str | None = Field(default=None, description="原始用户意图文本")


class TimerManageOutput(BaseModel):
    """计时器管理输出。"""

    summary: str
    task_id: str | None = None
    state: str | None = None


class TimerManageTool(BaseTool):
    """统一封装计时器相关复合流程。

    主要功能：
    1. 对模型暴露一个稳定的高层入口 `timer_manage`。
    2. 在内部继续复用 `create_timer`、`query_task_status`、`cancel_task` 等底层 Tool。

    主要方法：
    1. `run`：根据输入决定具体动作，并转发到底层 Tool。
    2. `_resolve_action`：推断本轮意图属于创建、查询还是取消。
    3. `_parse_duration_seconds`：从自然语言中提取秒数或分钟数。
    """

    spec = ToolSpec(
        name="timer_manage",
        description="当用户要创建、查询或取消计时器时使用。",
        input_model=TimerManageInput,
        output_model=TimerManageOutput,
        capability_type="tool",
        tags=["timer", "task"],
    )

    def run(self, context: AgentToolContext, input_data: TimerManageInput) -> CapabilityResult:
        """执行计时器管理逻辑。

        主要逻辑：
        1. 先根据显式参数或原始文本推断操作类型。
        2. 创建时调用 `create_timer`。
        3. 查询时调用 `query_task_status`。
        4. 取消时调用 `cancel_task`。

        参数：
        1. `context`：能力调用上下文。
        2. `input_data`：计时器管理输入。

        返回值：
        1. 统一 `CapabilityResult`，其中包含播报摘要和任务状态。

        异常情况：
        1. 未配置 `ToolGateway` 时抛出配置错误。
        2. 必填字段缺失时抛出消息错误。
        """

        if context.tool_gateway is None:
            raise build_error(ErrorCode.INVALID_CONFIG, "ToolGateway 未配置，无法管理计时器")

        action = self._resolve_action(input_data)
        if action == "create":
            duration_seconds = input_data.duration_seconds or self._parse_duration_seconds(input_data.query or "")
            if duration_seconds is None:
                raise build_error(ErrorCode.INVALID_MESSAGE, "创建计时器需要 duration_seconds")
            result = context.tool_gateway.invoke(
                name="create_timer",
                context=context,
                arguments={
                    "duration_seconds": duration_seconds,
                    "label": input_data.label,
                },
            )
            summary = f"计时器已创建，任务编号是 {result.data['task_id']}。"
            return CapabilityResult.success(
                data={
                    "summary": summary,
                    "task_id": result.data["task_id"],
                    "state": result.data["state"],
                },
                message=summary,
            )

        if action == "query":
            if not input_data.task_id:
                raise build_error(ErrorCode.INVALID_MESSAGE, "查询计时器需要 task_id")
            result = context.tool_gateway.invoke(
                name="query_task_status",
                context=context,
                arguments={"task_id": input_data.task_id},
            )
            return CapabilityResult.success(
                data={
                    "summary": result.data["summary"],
                    "task_id": result.data["task_id"],
                    "state": result.data["state"],
                },
                message=result.data["summary"],
            )

        if not input_data.task_id:
            raise build_error(ErrorCode.INVALID_MESSAGE, "取消计时器需要 task_id")
        result = context.tool_gateway.invoke(
            name="cancel_task",
            context=context,
            arguments={"task_id": input_data.task_id},
        )
        return CapabilityResult.success(
            data={
                "summary": result.data["summary"],
                "task_id": result.data["task_id"],
                "state": result.data["state"],
            },
            message=result.data["summary"],
        )

    @staticmethod
    def _resolve_action(input_data: TimerManageInput) -> str:
        """根据输入推断管理动作。"""

        if input_data.action != "auto":
            return input_data.action
        query = (input_data.query or "").strip()
        if "取消" in query:
            return "cancel"
        if input_data.task_id:
            return "query"
        return "create"

    @staticmethod
    def _parse_duration_seconds(query: str) -> int | None:
        """从中文文本中提取计时器秒数。"""

        digits = []
        current_digits = ""
        for char in query:
            if char.isdigit():
                current_digits += char
            elif current_digits:
                digits.append(int(current_digits))
                current_digits = ""
        if current_digits:
            digits.append(int(current_digits))
        if not digits:
            return None
        first = digits[0]
        return first * 60 if "分钟" in query else first
