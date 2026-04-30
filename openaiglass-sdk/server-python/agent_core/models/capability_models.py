"""agent-core 能力层公共对象模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from agent_core.context.models import DerivedArtifact, MediaAssetRef, TaskRef

ProgressMessage = str | list[str]


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
    capability_type: Literal["tool", "mcp", "task"] = "tool"
    tags: list[str] = field(default_factory=list)
    progress_message: ProgressMessage | None = None


def normalize_progress_messages(progress_message: ProgressMessage | None) -> list[str]:
    """规范化工具前置播报候选文案。

    主要逻辑：
    1. 兼容旧版单字符串写法。
    2. 支持新版字符串列表写法，用于运行时随机选择。
    3. 去掉空白文案和重复文案，避免缓存与播报重复。

    参数：
    1. `progress_message`：Tool 声明的前置播报配置。

    返回值：
    1. 清理后的播报候选列表。

    异常情况：
    1. 非字符串或非字符串列表会被忽略，不抛出业务异常。
    """

    if isinstance(progress_message, str):
        candidates = [progress_message]
    elif isinstance(progress_message, list):
        candidates = [item for item in progress_message if isinstance(item, str)]
    else:
        return []
    messages: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        message = candidate.strip()
        if not message or message in seen:
            continue
        messages.append(message)
        seen.add(message)
    return messages


@dataclass(slots=True)
class McpMethodSpec:
    """MCP 方法元数据定义。"""

    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel] | None = None
    tags: list[str] = field(default_factory=list)


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
