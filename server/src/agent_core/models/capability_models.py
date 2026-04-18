"""agent-core 能力层公共对象模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from agent_core.context.models import DerivedArtifact, MediaAssetRef, TaskRef


@dataclass(slots=True)
class CapabilityError:
    """能力调用错误对象。"""

    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CapabilityResult:
    """能力调用统一返回对象。"""

    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    asset_refs: list[MediaAssetRef] = field(default_factory=list)
    derived_artifacts: list[DerivedArtifact] = field(default_factory=list)
    task_refs: list[TaskRef] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    error: CapabilityError | None = None

    @classmethod
    def success(
        cls,
        *,
        data: dict[str, Any] | None = None,
        message: str = "",
        asset_refs: list[MediaAssetRef] | None = None,
        derived_artifacts: list[DerivedArtifact] | None = None,
        task_refs: list[TaskRef] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> "CapabilityResult":
        """构造成功结果。"""

        return cls(
            ok=True,
            data=data or {},
            message=message,
            asset_refs=asset_refs or [],
            derived_artifacts=derived_artifacts or [],
            task_refs=task_refs or [],
            meta=meta or {},
        )

    @classmethod
    def failed(
        cls,
        *,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> "CapabilityResult":
        """构造失败结果。"""

        return cls(
            ok=False,
            message=message,
            meta=meta or {},
            error=CapabilityError(code=code, message=message, details=details or {}),
        )


@dataclass(slots=True)
class ToolSpec:
    """Tool 元数据定义。"""

    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel] | None = None
    capability_type: Literal["tool", "skill", "mcp", "task"] = "tool"
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SkillSpec:
    """Skill 元数据定义。"""

    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel] | None = None
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class McpMethodSpec:
    """MCP 方法元数据定义。"""

    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel] | None = None
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SkillCall:
    """Skill 调用记录。"""

    skill_name: str
    session_id: str
    turn_id: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SkillResultRecord:
    """Skill 调用结果记录。"""

    call: SkillCall
    status: Literal["result", "failed"]
    result: CapabilityResult | None = None
    error: CapabilityError | None = None


@dataclass(slots=True)
class McpCall:
    """MCP 调用记录。"""

    method_name: str
    session_id: str
    turn_id: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class McpResultRecord:
    """MCP 调用结果记录。"""

    call: McpCall
    status: Literal["result", "failed"]
    result: CapabilityResult | None = None
    error: CapabilityError | None = None
