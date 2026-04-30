"""读取 Skill 文档 Tool。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agent_core.models import CapabilityResult, ToolSpec
from agent_core.skills import SkillRuntime
from agent_core.tools.base import AgentToolContext, BaseTool
from infra.errors import ErrorCode, build_error


class ReadSkillInput(BaseModel):
    """读取 Skill 输入。"""

    skill_name: str = Field(description="要读取的 Skill 名称；应填写用户当前任务可能需要的已注册 Skill 名称。")


class ReadSkillTool(BaseTool):
    """把 Skill 文档读取能力暴露给模型。"""

    def __init__(self, skill_runtime: SkillRuntime) -> None:
        self._skill_runtime = skill_runtime
        self.spec = ToolSpec(
            name="read_skill",
            description=(
                "当当前任务需要先了解某个已注册 Skill 的能力边界、调用步骤或可用工具时调用。"
                "读取后再按 Skill 文档继续执行任务。"
            ),
            input_model=ReadSkillInput,
            capability_type="skill",
            tags=["skill"],
        )

    def run(self, context: AgentToolContext, input_data: ReadSkillInput) -> CapabilityResult:
        """读取 Skill 文档并激活当前会话。

        参数：
        1. `context`：当前 Agent 工具上下文。
        2. `input_data`：包含 Skill 名称的输入模型。

        返回值：
        1. `CapabilityResult`，包含 Skill 元数据和正文。

        异常情况：
        1. Skill 不存在或被策略拒绝时抛出结构化错误。
        """

        try:
            document = self._skill_runtime.read_skill(input_data.skill_name)
            state = self._skill_runtime.activate_skill(
                session_id=context.session_id,
                skill_name=document.manifest.name,
            )
        except ValueError as exc:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "读取 Skill 失败",
                details={"skill_name": input_data.skill_name, "reason": str(exc)},
            ) from exc

        manifest = document.manifest
        return CapabilityResult.success(
            data={
                "name": manifest.name,
                "version": manifest.version,
                "description": manifest.description,
                "entrypoint": manifest.entrypoint,
                "allowed_tools": list(manifest.allowed_tools),
                "allowed_mcp_methods": list(manifest.allowed_mcp_methods),
                "content": document.content,
                "active_skill_names": list(state.active_skill_names),
            }
        )
