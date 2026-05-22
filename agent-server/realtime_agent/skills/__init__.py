from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from realtime_agent.errors import RealtimeAgentError, ErrorCode


@dataclass(frozen=True)
class SkillDocument:
    """受控 Skill 文档。

    主要功能：承载可被 `read_skill` 工具读取的技能说明。
    主要属性：`name/description/content` 给模型使用，`tool_allowlist`
    和 `prompt_snippets` 给 ToolGateway 或上层上下文构造器使用。
    """

    name: str
    description: str = ""
    content: str = ""
    tool_allowlist: list[str] = field(default_factory=list)
    prompt_snippets: list[str] = field(default_factory=list)
    path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class SkillError(RealtimeAgentError):
    """Skill Service 结构化异常。"""


class SkillService:
    """受控 Skill 读取服务。

    主要功能：只允许读取配置 roots 下的 Skill 文档，并暴露 metadata。
    主要约束：本服务不持有 `ToolDeviceFacade`，需要设备通讯能力时必须由普通
    Tool 或 Task 间接完成。
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        roots: list[str | Path] | None = None,
        allow_tool_policy: bool = True,
    ) -> None:
        self.enabled = enabled
        self.roots = [Path(root).resolve() for root in roots or []]
        self.allow_tool_policy = allow_tool_policy

    def read_skill(self, name: str) -> SkillDocument:
        """读取一个 Skill 文档。

        主要逻辑：拒绝路径穿越，只在 roots/name 下查找 `SKILL.md`、
        `skill.md`、`README.md` 或 metadata 文件。
        参数：`name` 为 skill 目录名。
        返回值：`SkillDocument`。
        异常情况：未启用、名称非法或未找到时抛出 `SkillError`。
        """

        self._ensure_enabled()
        safe_name = self._validate_name(name)
        for root in self.roots:
            document = self._load_from_dir(root / safe_name, fallback_name=safe_name)
            if document is not None:
                return document
        raise SkillError("skill not found", code=ErrorCode.NOT_FOUND, details={"name": safe_name})

    def tool_allowlist(self) -> set[str]:
        """读取所有 Skill 声明的工具白名单。

        返回值：当 `allow_tool_policy=false` 或无白名单时返回空集合。
        异常情况：单个 Skill 损坏时跳过，避免启动期被非活跃 Skill 阻塞。
        """

        if not self.enabled or not self.allow_tool_policy:
            return set()
        allowed: set[str] = set()
        for root in self.roots:
            if not root.exists():
                continue
            for child in root.iterdir():
                if not child.is_dir():
                    continue
                document = self._load_from_dir(child, fallback_name=child.name)
                if document is not None:
                    allowed.update(document.tool_allowlist)
        return allowed

    def _ensure_enabled(self) -> None:
        if not self.enabled:
            raise SkillError("skill service is disabled", code=ErrorCode.PERMISSION_DENIED)

    @staticmethod
    def _validate_name(name: str) -> str:
        safe_name = name.strip()
        if not safe_name or "/" in safe_name or "\\" in safe_name or safe_name in {".", ".."}:
            raise SkillError("invalid skill name", code=ErrorCode.INVALID_ARGUMENT, details={"name": name})
        return safe_name

    def _load_from_dir(self, directory: Path, *, fallback_name: str) -> SkillDocument | None:
        directory = directory.resolve()
        if not self._is_under_roots(directory) or not directory.exists() or not directory.is_dir():
            return None
        metadata = self._load_metadata(directory)
        content_path = self._first_existing(directory, ("SKILL.md", "skill.md", "README.md"))
        content = content_path.read_text(encoding="utf-8") if content_path else ""
        frontmatter, body = self._split_frontmatter(content)
        metadata = {**metadata, **frontmatter}
        name = str(metadata.get("name") or fallback_name)
        return SkillDocument(
            name=name,
            description=str(metadata.get("description") or ""),
            content=body.strip(),
            tool_allowlist=list(metadata.get("tool_allowlist") or metadata.get("tools") or []),
            prompt_snippets=list(metadata.get("prompt_snippets") or metadata.get("prompts") or []),
            path=str(directory),
            metadata=metadata,
        )

    def _is_under_roots(self, path: Path) -> bool:
        for root in self.roots:
            try:
                path.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    @staticmethod
    def _first_existing(directory: Path, names: tuple[str, ...]) -> Path | None:
        for name in names:
            candidate = directory / name
            if candidate.exists() and candidate.is_file():
                return candidate
        return None

    def _load_metadata(self, directory: Path) -> dict[str, Any]:
        for name in ("skill.yaml", "skill.yml", "skill.json"):
            candidate = directory / name
            if not candidate.exists():
                continue
            if candidate.suffix == ".json":
                return dict(json.loads(candidate.read_text(encoding="utf-8")) or {})
            return dict(yaml.safe_load(candidate.read_text(encoding="utf-8")) or {})
        return {}

    @staticmethod
    def _split_frontmatter(content: str) -> tuple[dict[str, Any], str]:
        if not content.startswith("---\n"):
            return {}, content
        _, rest = content.split("---\n", 1)
        if "\n---\n" not in rest:
            return {}, content
        raw_meta, body = rest.split("\n---\n", 1)
        return dict(yaml.safe_load(raw_meta) or {}), body


__all__ = ["SkillDocument", "SkillError", "SkillService"]
