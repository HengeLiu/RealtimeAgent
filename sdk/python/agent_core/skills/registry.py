"""Skill 注册表。"""

from __future__ import annotations

from collections.abc import Iterable

from agent_core.skills.models import SkillDocument, SkillManifest


class SkillRegistry:
    """保存已注册 Skill 文档。

    主要功能：
    1. 注册外部项目提供的 Skill 文档。
    2. 按名称读取 Skill。
    3. 列出当前可用 Skill。
    """

    def __init__(self) -> None:
        self._documents: dict[str, SkillDocument] = {}

    def register(self, document: SkillDocument) -> None:
        """注册 Skill 文档。

        参数：
        1. `document`：待注册的 Skill 文档。

        异常情况：
        1. Skill 名称为空时抛出 `ValueError`。
        2. Skill 名称重复时抛出 `ValueError`。
        """

        name = str(document.manifest.name).strip()
        if not name:
            raise ValueError("Skill 名称不能为空")
        if name in self._documents:
            raise ValueError(f"Skill 重复注册: {name}")
        self._documents[name] = document

    def register_manifest(self, manifest: SkillManifest, content: str = "") -> None:
        """基于 manifest 快速注册 Skill。"""

        self.register(SkillDocument(manifest=manifest, content=content))

    def get(self, name: str) -> SkillDocument | None:
        """按名称读取 Skill 文档。"""

        return self._documents.get(str(name).strip())

    def list(self) -> list[SkillDocument]:
        """列出全部 Skill 文档。"""

        return [self._documents[name] for name in sorted(self._documents)]

    def names(self) -> list[str]:
        """列出全部 Skill 名称。"""

        return sorted(self._documents)

    def extend(self, documents: Iterable[SkillDocument]) -> None:
        """批量注册 Skill 文档。"""

        for document in documents:
            self.register(document)

