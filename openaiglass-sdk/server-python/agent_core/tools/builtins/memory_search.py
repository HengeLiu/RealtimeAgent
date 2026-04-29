"""按标题读取冷记忆详情的 Tool。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agent_core.memory import AgentMemoryRuntime
from agent_core.models import CapabilityResult, ToolSpec
from agent_core.tools.base import AgentToolContext, BaseTool


class MemorySearchInput(BaseModel):
    """冷记忆详情查询输入。"""

    title: str = Field(default="", description="单个冷记忆标题")
    titles: list[str] = Field(default_factory=list, description="多个冷记忆标题")


class MemorySearchTool(BaseTool):
    """按标题读取冷记忆详情。

    主要功能：
    1. 主 Agent 每轮只会看到冷记忆标题。
    2. 当模型判断需要某项冷记忆详情时，通过本工具按标题读取内容。
    3. 本工具不负责新增、更新或删除记忆。
    """

    spec = ToolSpec(
        name="memory_search",
        description="按标题读取冷记忆详情。入参可以是 title 或 titles；不要用它新增、更新或删除记忆。",
        input_model=MemorySearchInput,
        capability_type="tool",
        tags=["memory"],
    )

    def __init__(self, memory_runtime: AgentMemoryRuntime) -> None:
        self._memory_runtime = memory_runtime

    def run(self, context: AgentToolContext, input_data: MemorySearchInput) -> CapabilityResult:
        """读取冷记忆详情。"""

        titles = list(input_data.titles)
        if input_data.title.strip():
            titles.insert(0, input_data.title)
        records = self._memory_runtime.search_cold_memories_by_title(
            scope_type="device",
            scope_id=context.device_id,
            titles=titles,
        )
        return CapabilityResult.success(
            data={"memories": [AgentMemoryRuntime.record_to_dict(record) for record in records]},
            message="已读取冷记忆详情",
        )
