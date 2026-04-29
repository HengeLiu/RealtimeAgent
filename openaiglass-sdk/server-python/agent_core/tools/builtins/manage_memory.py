"""管理 Agent 长期记忆的 Tool。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from agent_core.memory import AgentMemoryRuntime
from agent_core.models import CapabilityResult, ToolSpec
from agent_core.tools.base import AgentToolContext, BaseTool
from infra.errors import ErrorCode, build_error


class ManageMemoryInput(BaseModel):
    """长期记忆管理输入。"""

    action: Literal["add", "search", "list", "delete"] = Field(description="操作类型")
    text: str = Field(default="", description="新增记忆时写入的短句")
    query: str = Field(default="", description="查询记忆时使用的关键词")
    memory_id: str = Field(default="", description="删除记忆时使用的记忆编号")
    category: str = Field(default="general", description="记忆类别，例如 profile、preference、habit")
    source: Literal["user_requested", "agent_inferred", "system"] = Field(
        default="agent_inferred",
        description="记忆来源",
    )
    limit: int = Field(default=5, ge=1, le=20, description="查询返回数量上限")


class ManageMemoryTool(BaseTool):
    """把长期记忆管理能力暴露给模型。

    主要功能：
    1. 当用户主动要求记住、修改或删除信息时，模型可调用本工具。
    2. 当模型发现明确且稳定的用户偏好时，也可以主动写入记忆。
    3. 记忆按当前设备隔离，避免不同用户或设备互相污染。
    """

    spec = ToolSpec(
        name="manage_memory",
        description=(
            "管理用户长期记忆。用户明确要求记住、忘记、删除、查看记忆时必须调用；"
            "发现稳定的用户基本信息、偏好或行为习惯时也可新增短记忆。"
        ),
        input_model=ManageMemoryInput,
        capability_type="tool",
        tags=["memory"],
    )

    def __init__(self, memory_runtime: AgentMemoryRuntime) -> None:
        self._memory_runtime = memory_runtime

    def run(self, context: AgentToolContext, input_data: ManageMemoryInput) -> CapabilityResult:
        """执行长期记忆管理。

        主要逻辑：
        1. 使用当前 `device_id` 作为默认记忆作用域。
        2. 根据 `action` 新增、查询、列出或软删除记忆。
        3. 返回结构化记忆记录，便于模型向用户确认。

        参数：
        1. `context`：当前工具上下文。
        2. `input_data`：记忆管理参数。

        返回值：
        1. `CapabilityResult`。

        异常情况：
        1. 记忆运行时缺失、参数不完整或记忆不存在时抛出结构化错误。
        """

        scope_type = "device"
        scope_id = context.device_id
        if input_data.action == "add":
            try:
                record = self._memory_runtime.add_memory(
                    scope_type=scope_type,
                    scope_id=scope_id,
                    text=input_data.text,
                    category=input_data.category,
                    source=input_data.source,
                    metadata={"session_id": context.session_id, "turn_id": context.turn_id},
                )
            except ValueError as exc:
                raise build_error(ErrorCode.INVALID_MESSAGE, str(exc)) from exc
            return CapabilityResult.success(
                data={"memory": AgentMemoryRuntime.record_to_dict(record)},
                message="已写入长期记忆",
            )

        if input_data.action == "delete":
            deleted = self._memory_runtime.delete_memory(
                memory_id=input_data.memory_id,
                scope_type=scope_type,
                scope_id=scope_id,
            )
            if deleted is None:
                raise build_error(
                    ErrorCode.INVALID_MESSAGE,
                    "目标记忆不存在或不属于当前设备",
                    details={"memory_id": input_data.memory_id},
                )
            return CapabilityResult.success(
                data={"memory": AgentMemoryRuntime.record_to_dict(deleted)},
                message="已删除长期记忆",
            )

        if input_data.action == "list":
            records = self._memory_runtime.list_memories(
                scope_type=scope_type,
                scope_id=scope_id,
                limit=input_data.limit,
            )
        else:
            records = self._memory_runtime.search_memories(
                scope_type=scope_type,
                scope_id=scope_id,
                query=input_data.query,
                limit=input_data.limit,
            )
        return CapabilityResult.success(
            data={"memories": [AgentMemoryRuntime.record_to_dict(record) for record in records]},
            message="已读取长期记忆",
        )
