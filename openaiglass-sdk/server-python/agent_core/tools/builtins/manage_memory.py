"""管理 Agent 长期记忆的 Tool。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agent_core.memory import AgentMemoryRuntime, MemoryOperationRequest
from agent_core.models import CapabilityResult, ToolSpec
from agent_core.tools.base import AgentToolContext, BaseTool
from infra.errors import ErrorCode, build_error


class ManageMemoryInput(BaseModel):
    """长期记忆管理输入。"""

    query: str = Field(description="用户关于记忆管理的原始自然语言指令")
    memory_context: str = Field(
        default="",
        description="主 Agent 从历史聊天中摘取的、与记忆维护有关的关键信息；没有时留空",
    )


class ManageMemoryTool(BaseTool):
    """把长期记忆管理能力暴露给模型。

    主要功能：
    1. 承载除搜索之外的记忆管理能力。
    2. 内部把自然语言请求交给记忆管理子 Agent，由子 Agent 自行决定动作列表。
    3. 完成后只返回简短文本反馈和不含内部编号的动作摘要。
    """

    spec = ToolSpec(
        name="manage_memory",
        description=(
            "管理长期记忆。用于处理用户要求记住、更新、忘记或删除记忆的自然语言请求；"
            "只需要传入用户原始请求和相关聊天上下文，不要自行提取memory_id或结构化字段。"
            "查询记忆详情必须使用 memory_search。"
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
                    query=input_data.query,
                    memory_context=input_data.memory_context,
                    metadata={"session_id": context.session_id, "turn_id": context.turn_id},
                ),
            )
        except ValueError as exc:
            raise build_error(ErrorCode.INVALID_MESSAGE, str(exc)) from exc
        feedback = str(result.get("feedback") or "记忆已处理")
        return CapabilityResult.success(
            data=result,
            message=feedback,
        )
