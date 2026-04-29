"""管理 Agent 长期记忆的 Tool。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from agent_core.memory import AgentMemoryRuntime, MemoryOperationRequest
from agent_core.models import CapabilityResult, ToolSpec
from agent_core.tools.base import AgentToolContext, BaseTool
from infra.errors import ErrorCode, build_error


class ManageMemoryInput(BaseModel):
    """长期记忆管理输入。"""

    operation: Literal["add", "update", "delete"] = Field(description="期望执行的记忆操作")
    query: str = Field(description="用户关于记忆管理的原始自然语言指令")
    preferred_memory_type: Literal["hot", "cold"] | None = Field(
        default=None,
        description="调用方可选的冷热记忆偏好；不确定时留空，由记忆管理子 Agent 判断",
    )
    title: str = Field(default="", description="可选记忆标题，例如姓名、住址、喜欢的食物")
    content: str = Field(default="", description="可选记忆内容；不确定时可只传 query")
    memory_id: str = Field(default="", description="删除或更新时可选的记忆编号")
    category: str = Field(default="general", description="记忆类别，例如 profile、preference、habit")
    source: Literal["user_requested", "agent_inferred", "system"] = Field(
        default="user_requested",
        description="记忆来源",
    )


class ManageMemoryTool(BaseTool):
    """把长期记忆管理能力暴露给模型。

    主要功能：
    1. 承载除搜索之外的记忆管理能力。
    2. 内部把请求交给记忆管理子 Agent，由子 Agent 决定冷热分类和具体操作内容。
    3. 完成后只返回操作完成状态和被影响的记忆摘要。
    """

    spec = ToolSpec(
        name="manage_memory",
        description=(
            "管理长期记忆。用于新增、更新或删除记忆；不要用它查询冷记忆详情，"
            "查询冷记忆详情必须使用 memory_search。"
        ),
        input_model=ManageMemoryInput,
        capability_type="tool",
        tags=["memory"],
    )

    def __init__(self, memory_runtime: AgentMemoryRuntime) -> None:
        self._memory_runtime = memory_runtime

    def run(self, context: AgentToolContext, input_data: ManageMemoryInput) -> CapabilityResult:
        """执行长期记忆管理。

        参数：
        1. `context`：当前工具上下文。
        2. `input_data`：记忆管理请求。

        返回值：
        1. `CapabilityResult`，表示记忆管理已完成。

        异常情况：
        1. 记忆关闭、参数不完整或删除目标不存在时抛出结构化错误。
        """

        try:
            result = self._memory_runtime.manage_memory(
                scope_type="device",
                scope_id=context.device_id,
                request=MemoryOperationRequest(
                    operation=input_data.operation,
                    query=input_data.query,
                    preferred_memory_type=input_data.preferred_memory_type,
                    title=input_data.title,
                    content=input_data.content,
                    memory_id=input_data.memory_id,
                    category=input_data.category,
                    source=input_data.source,
                    metadata={"session_id": context.session_id, "turn_id": context.turn_id},
                ),
            )
        except ValueError as exc:
            raise build_error(ErrorCode.INVALID_MESSAGE, str(exc)) from exc
        if input_data.operation == "delete" and result.get("memory") is None:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "没有找到需要删除的记忆",
                details={"query": input_data.query, "title": input_data.title, "memory_id": input_data.memory_id},
            )
        return CapabilityResult.success(
            data=result,
            message="记忆管理已完成",
        )
