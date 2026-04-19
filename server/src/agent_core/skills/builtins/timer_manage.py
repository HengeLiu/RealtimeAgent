"""计时器管理 Skill。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from agent_core.models import CapabilityResult, SkillSpec
from agent_core.skills.base import BaseSkill
from infra.errors import ErrorCode, build_error


class TimerManageInput(BaseModel):
    """计时器管理输入。"""

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


class TimerManageSkill(BaseSkill):
    """封装计时器创建、查询和取消。"""

    spec = SkillSpec(
        name="timer_manage",
        description="当用户要创建、查询或取消计时器时使用。",
        input_model=TimerManageInput,
        output_model=TimerManageOutput,
        tags=["timer", "task"],
    )

    def run(self, context, input_data: TimerManageInput) -> CapabilityResult:
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
