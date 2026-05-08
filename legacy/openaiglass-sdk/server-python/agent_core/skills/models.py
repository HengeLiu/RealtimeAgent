"""Skill Runtime 的基础数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SkillManifest:
    """描述一个 Skill 的基础元数据。

    主要功能：
    1. 保存 Skill 的名称、版本、描述和入口信息。
    2. 作为 Skill 注册表与策略判断的轻量契约。

    主要属性：
    1. `name`：Skill 唯一名称。
    2. `version`：Skill 版本。
    3. `description`：给模型和开发者阅读的能力说明。
    4. `entrypoint`：能力入口或文档路径。
    5. `allowed_tools`：该 Skill 激活时允许模型调用的 Tool 名称。
    6. `allowed_mcp_methods`：该 Skill 激活时允许模型调用的 MCP 方法名。
    7. `metadata`：额外扩展信息。
    """

    name: str
    version: str = "0.1.0"
    description: str = ""
    entrypoint: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    allowed_mcp_methods: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SkillDocument:
    """保存 Skill 文档内容。

    主要功能：
    1. 把 Skill manifest 与正文内容放在同一个对象里。
    2. 供后续 `read_skill` 或 Skill Registry 读取。
    """

    manifest: SkillManifest
    content: str = ""


@dataclass(slots=True)
class SkillSessionState:
    """保存单个会话内的 Skill 激活状态。

    主要功能：
    1. 记录当前会话已经激活的 Skill。
    2. 记录与 Skill 相关的轻量上下文。
    """

    session_id: str
    active_skill_names: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)

    def activate(self, skill_name: str) -> None:
        """激活指定 Skill。

        参数：
        1. `skill_name`：Skill 名称。

        返回值：
        1. 无。

        异常情况：
        1. 本函数不主动抛出异常。
        """

        normalized = str(skill_name).strip()
        if normalized and normalized not in self.active_skill_names:
            self.active_skill_names.append(normalized)

    def deactivate(self, skill_name: str) -> None:
        """取消激活指定 Skill。"""

        normalized = str(skill_name).strip()
        self.active_skill_names = [item for item in self.active_skill_names if item != normalized]
