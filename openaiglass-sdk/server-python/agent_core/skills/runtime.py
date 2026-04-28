"""Skill Runtime 会话运行时。"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_core.skills.models import SkillDocument, SkillManifest, SkillSessionState
from agent_core.skills.policy import SkillPolicy
from agent_core.skills.registry import SkillRegistry


@dataclass(slots=True)
class SkillRuntime:
    """管理 Skill 注册、会话激活态和工具白名单。

    主要功能：
    1. 维护已注册 Skill 文档。
    2. 维护每个会话的 active Skill。
    3. 为 Agent prompt 生成 Skill 上下文片段。
    4. 根据 active Skill 计算模型可见工具集合。
    """

    registry: SkillRegistry = field(default_factory=SkillRegistry)
    policy: SkillPolicy = field(default_factory=SkillPolicy)
    _session_states: dict[str, SkillSessionState] = field(default_factory=dict)

    def register(self, document: SkillDocument) -> None:
        """注册 Skill 文档。"""

        self.registry.register(document)

    def register_manifest(self, manifest: SkillManifest, content: str = "") -> None:
        """基于 manifest 注册 Skill。"""

        self.registry.register_manifest(manifest, content=content)

    def list_skill_names(self) -> list[str]:
        """列出当前可用 Skill 名称。"""

        return self.registry.names()

    def read_skill(self, skill_name: str) -> SkillDocument:
        """读取 Skill 文档。

        异常情况：
        1. Skill 未注册或被策略拒绝时抛出 `ValueError`。
        """

        normalized = str(skill_name).strip()
        if not self.policy.is_allowed(normalized):
            raise ValueError(f"当前策略不允许读取 Skill: {normalized}")
        document = self.registry.get(normalized)
        if document is None:
            raise ValueError(f"Skill 不存在: {normalized}")
        return document

    def activate_skill(self, *, session_id: str, skill_name: str) -> SkillSessionState:
        """在指定会话激活 Skill。"""

        document = self.read_skill(skill_name)
        state = self.get_session_state(session_id)
        state.activate(document.manifest.name)
        return state

    def deactivate_skill(self, *, session_id: str, skill_name: str) -> SkillSessionState:
        """取消指定会话中的 active Skill。"""

        state = self.get_session_state(session_id)
        state.deactivate(skill_name)
        return state

    def get_session_state(self, session_id: str) -> SkillSessionState:
        """读取或创建会话 Skill 状态。"""

        normalized = str(session_id).strip()
        if not normalized:
            normalized = "default"
        return self._session_states.setdefault(normalized, SkillSessionState(session_id=normalized))

    def build_prompt_fragment(self, *, session_id: str) -> str:
        """生成可附加到 Agent system prompt 的 Skill 片段。"""

        documents = self._active_documents(session_id)
        if not documents:
            available = self._available_skill_summaries()
            if not available:
                return ""
            return "可用 Skills：\n" + "\n".join(available) + "\n需要时先调用 read_skill 读取具体说明。"

        parts = ["当前 active Skills："]
        for document in documents:
            manifest = document.manifest
            parts.append(f"- {manifest.name} v{manifest.version}: {manifest.description}")
            if document.content.strip():
                parts.append(document.content.strip())
        return "\n".join(parts)

    def allowed_tool_names_for_session(self, *, session_id: str) -> set[str] | None:
        """计算当前会话允许暴露给模型的工具名。

        返回值：
        1. `None` 表示没有 active Skill，沿用默认模型工具集合。
        2. 集合表示 active Skill 限定后的工具集合。
        """

        documents = self._active_documents(session_id)
        if not documents:
            return None

        allowed: set[str] = {"read_skill"}
        for document in documents:
            manifest = document.manifest
            allowed.update(str(item).strip() for item in manifest.allowed_tools if str(item).strip())
            allowed.update(
                str(item).replace(".", "_").strip()
                for item in manifest.allowed_mcp_methods
                if str(item).strip()
            )
        return allowed

    def build_snapshot(self) -> dict[str, object]:
        """构建 Skill Runtime 快照。"""

        return {
            "registered_skill_names": self.list_skill_names(),
            "sessions": [
                {
                    "session_id": state.session_id,
                    "active_skill_names": list(state.active_skill_names),
                    "context": dict(state.context),
                }
                for state in sorted(self._session_states.values(), key=lambda item: item.session_id)
            ],
        }

    def _active_documents(self, session_id: str) -> list[SkillDocument]:
        """读取当前会话 active Skill 文档。"""

        state = self.get_session_state(session_id)
        documents: list[SkillDocument] = []
        for skill_name in state.active_skill_names:
            if not self.policy.is_allowed(skill_name):
                continue
            document = self.registry.get(skill_name)
            if document is not None:
                documents.append(document)
        return documents

    def _available_skill_summaries(self) -> list[str]:
        """生成可用 Skill 摘要。"""

        summaries: list[str] = []
        for document in self.registry.list():
            manifest = document.manifest
            if not self.policy.is_allowed(manifest.name):
                continue
            summaries.append(f"- {manifest.name} v{manifest.version}: {manifest.description}")
        return summaries
