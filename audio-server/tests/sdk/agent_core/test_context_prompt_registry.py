from __future__ import annotations

from pathlib import Path

import pytest

from audio_chat.agent_core.context import PromptRegistry


def test_prompt_registry_loads_flat_prompt_assets() -> None:
    """测试目标：验证默认 PromptRegistry 能加载平铺提示词资产。

    测试方法：读取 SDK 包内 registry，检查关键 prompt 名称和正文。
    预期结果：name 唯一、文件存在，且能按 name 读取 Markdown 正文。
    """

    registry = PromptRegistry()
    prompts = registry.load_all()

    assert "realtime_system" in prompts
    assert "text_system" in prompts
    assert "memory_rules" in prompts
    assert "realtime_tool_call_rules" in prompts
    assert prompts["realtime_system"].content
    assert prompts["memory_rules"].file == "memory_rules.md"


def test_prompt_registry_rejects_nested_prompt_file(tmp_path: Path) -> None:
    """测试目标：验证第一版 prompts 目录保持平铺。

    测试方法：构造一个 file 带子目录的 registry。
    预期结果：load_all 明确失败，防止重新引入复杂目录分类。
    """

    (tmp_path / "registry.yaml").write_text(
        "prompts:\n"
        "  - name: bad\n"
        "    file: nested/bad.md\n"
        "    description: bad\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="flat prompts directory"):
        PromptRegistry(tmp_path).load_all()
