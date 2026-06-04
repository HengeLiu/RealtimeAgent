from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from realtime_agent.conversation.context.models import PromptAsset


class PromptRegistry:
    """repo 内 YAML + Markdown prompt 注册表。

    主要功能：从 `realtime_agent/prompts/registry.yaml` 读取 prompt metadata，再按 `name`
    返回同目录 Markdown 正文。第一版只支持本地文件。
    """

    def __init__(self, root: str | Path | None = None) -> None:
        """创建 PromptRegistry。

        参数：`root` 为 prompts 目录；为空时使用 SDK 包内默认目录。
        返回值：无。
        异常情况：registry 文件缺失、格式错误或文件缺失时由 `load_all()` 抛出。
        """

        self.root = Path(root).expanduser().resolve() if root is not None else _default_prompt_root()
        self.registry_path = self.root / "registry.yaml"
        self._cache: dict[str, PromptAsset] | None = None

    def load_all(self) -> dict[str, PromptAsset]:
        """读取并校验全部 prompt。

        主要逻辑：校验 registry 顶层 `prompts` 列表、`name` 唯一、file 存在。
        返回值：按 name 索引的 PromptAsset 字典。
        异常情况：配置缺失、重复 name 或文件缺失时抛出 ValueError/FileNotFoundError。
        """

        if self._cache is not None:
            return dict(self._cache)
        if not self.registry_path.is_file():
            raise FileNotFoundError(f"prompt registry not found: {self.registry_path}")
        raw = yaml.safe_load(self.registry_path.read_text(encoding="utf-8")) or {}
        prompts = raw.get("prompts")
        if not isinstance(prompts, list):
            raise ValueError("prompt registry must contain a prompts list")
        loaded: dict[str, PromptAsset] = {}
        for item in prompts:
            if not isinstance(item, dict):
                raise ValueError("prompt registry item must be a mapping")
            name = str(item.get("name") or "").strip()
            file_name = str(item.get("file") or "").strip()
            description = str(item.get("description") or "").strip()
            if not name:
                raise ValueError("prompt registry item missing name")
            if name in loaded:
                raise ValueError(f"duplicate prompt name: {name}")
            if not file_name:
                raise ValueError(f"prompt {name} missing file")
            if "/" in file_name or "\\" in file_name:
                raise ValueError(f"prompt file must be in flat prompts directory: {file_name}")
            path = self.root / file_name
            if not path.is_file():
                raise FileNotFoundError(f"prompt file not found for {name}: {path}")
            loaded[name] = PromptAsset(
                name=name,
                file=file_name,
                description=description,
                content=path.read_text(encoding="utf-8").strip(),
            )
        self._cache = loaded
        return dict(loaded)

    def get(self, name: str) -> PromptAsset:
        """按 name 读取 prompt。"""

        normalized = str(name or "").strip()
        prompts = self.load_all()
        if normalized not in prompts:
            raise KeyError(f"unknown prompt name: {normalized}")
        return prompts[normalized]

    def maybe_get(self, name: str) -> PromptAsset | None:
        """按 name 读取 prompt；不存在时返回 None。

        主要用于兼容配置仍使用 inline prompt 的阶段，避免上下文编译失败。
        """

        try:
            return self.get(name)
        except (FileNotFoundError, KeyError, ValueError):
            return None

    def list_records(self) -> list[dict[str, Any]]:
        """返回 registry 中全部 prompt 的摘要。"""

        return [asset.to_record() for asset in self.load_all().values()]


def _default_prompt_root() -> Path:
    return Path(__file__).resolve().parents[2] / "prompts"
