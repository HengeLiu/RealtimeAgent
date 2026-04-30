"""按主题读取记忆详情的 Tool。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agent_core.memory import AgentMemoryRuntime
from agent_core.models import CapabilityResult, ToolSpec
from agent_core.tools.base import AgentToolContext, BaseTool


class MemorySearchInput(BaseModel):
    """记忆详情查询输入。"""

    topic: str = Field(
        default="",
        description="要读取详情的单个记忆主题；优先使用系统提示中列出的记忆主题，或用户明确提到的记忆主题。",
    )
    topics: list[str] = Field(
        default_factory=list,
        description="要一次读取详情的多个记忆主题；只填写与当前回答直接相关的主题。",
    )


class MemorySearchTool(BaseTool):
    """按主题读取记忆详情。

    主要功能：
    1. 主 Agent 每轮只会看到部分记忆主题。
    2. 当模型判断需要某项记忆详情时，通过本工具按主题读取内容。
    3. 本工具不负责新增、更新或删除记忆。
    """

    spec = ToolSpec(
        name="memory_search",
        description=(
            "当回答用户问题需要读取已保存的长期记忆详情时调用。"
            "本工具只查询记忆，不用于新增、更新或删除记忆；维护记忆请使用 manage_memory。"
        ),
        input_model=MemorySearchInput,
        capability_type="tool",
        tags=["memory"],
    )

    def __init__(self, memory_runtime: AgentMemoryRuntime) -> None:
        self._memory_runtime = memory_runtime

    def run(self, context: AgentToolContext, input_data: MemorySearchInput) -> CapabilityResult:
        """读取记忆详情。"""

        topics = list(input_data.topics)
        if input_data.topic.strip():
            topics.insert(0, input_data.topic)
        records = self._memory_runtime.search_memories_by_topic(
            scope_type="device",
            scope_id=context.device_id,
            topics=topics,
        )
        memories = [AgentMemoryRuntime.record_to_public_dict(record) for record in records]
        feedback = "已读取记忆详情" if memories else "没有找到匹配的记忆"
        return CapabilityResult.success(
            data={"memories": memories, "feedback": feedback},
            message=feedback,
        )
