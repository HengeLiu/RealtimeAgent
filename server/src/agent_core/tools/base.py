"""Tool 层基础定义。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from agent_core.context.models import CapabilityTrace, DerivedArtifact, MediaAssetRef, TaskRef
from agent_core.models import CapabilityResult, ToolSpec
from infra.config import ServerSettings

if TYPE_CHECKING:
    from agent_core.camera import CameraGateway
    from agent_core.context import AgentSessionStore
    from agent_core.mcp import McpGateway
    from agent_core.tools.gateway import ToolGateway
    from backend_task_core import TaskGateway


@dataclass(slots=True)
class AgentToolContext:
    """能力调用上下文。"""

    session_id: str
    device_id: str
    turn_id: str
    settings: ServerSettings
    session_store: "AgentSessionStore | None"
    device_state_reader: Callable[[], dict[str, Any]]
    trace_sink: Callable[[CapabilityTrace], None]
    device_group_context_factory: Callable[..., Any] | None = None
    task_gateway: "TaskGateway | None" = None
    camera_gateway: "CameraGateway | None" = None
    tool_gateway: "ToolGateway | None" = None
    mcp_gateway: "McpGateway | None" = None
    emitted_assets: list[MediaAssetRef] = field(default_factory=list)
    emitted_artifacts: list[DerivedArtifact] = field(default_factory=list)
    emitted_tasks: list[TaskRef] = field(default_factory=list)

    def absorb(self, result: CapabilityResult) -> None:
        """吸收能力结果中的引用对象。"""

        self._extend_unique(self.emitted_assets, result.asset_refs, "asset_id")
        self._extend_unique(self.emitted_artifacts, result.derived_artifacts, "artifact_id")
        self._extend_unique(self.emitted_tasks, result.task_refs, "task_id")

    @staticmethod
    def _extend_unique(target: list[Any], source: list[Any], id_field: str) -> None:
        existing_ids = {getattr(item, id_field) for item in target}
        for item in source:
            item_id = getattr(item, id_field)
            if item_id in existing_ids:
                continue
            target.append(item)
            existing_ids.add(item_id)


class BaseTool(ABC):
    """所有模型可见 Tool 的基类。"""

    spec: ToolSpec

    @abstractmethod
    def run(self, context: AgentToolContext, input_data) -> CapabilityResult:
        """执行 Tool 逻辑。"""


class BaseMcpTool(BaseTool):
    """MCP Tool 基类。"""


class BaseTaskTool(BaseTool):
    """Task Tool 基类。"""
