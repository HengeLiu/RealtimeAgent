from __future__ import annotations

import sys
from pathlib import Path

import pytest

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.tasks import BaseTask, TaskAutoDiscovery
from audio_chat.tools import BaseTool, ToolAutoDiscovery, ToolError


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "for_blind_app"


def clear_capabilities_modules() -> None:
    """清理测试中复用的 `capabilities` 模块缓存。"""

    for name in list(sys.modules):
        if name == "capabilities" or name.startswith("capabilities."):
            sys.modules.pop(name, None)


def test_for_blind_app_auto_discovery_registers_tool_and_tasks(monkeypatch, tmp_path) -> None:
    """测试目标：确认开发者新增能力后不修改 server 内部代码即可自动注册。

    测试方法：把测试 app-root 加入 sys.path，配置 Tool / Task 递归发现 `capabilities`。
    预期结果：`capture_photo` Tool、`timer` Task 和 `continuous_rgb_analyze` Task 都进入注册表。
    """

    clear_capabilities_modules()
    monkeypatch.syspath_prepend(str(FIXTURE_ROOT))
    config = AudioChatConfig(
        runs_root=str(tmp_path / "runs"),
        asset_root=str(tmp_path / "runs" / "assets"),
        tools_discover_enabled=True,
        tools_discover_packages=("capabilities",),
        tools_discover_recursive=True,
        tasks_discover_enabled=True,
        tasks_discover_packages=("capabilities",),
        tasks_discover_recursive=True,
    )

    app = AudioChatApp(config)

    assert "capture_photo" in app.tool_registry.list_names()
    assert "timer" in app.task_engine.registry.list_task_types()
    assert "continuous_rgb_analyze" in app.task_engine.registry.list_task_types()
    assert app.discovery_errors == []


def test_for_blind_app_discovery_contract_skips_internal_and_fails_duplicates(tmp_path, monkeypatch) -> None:
    """测试目标：冻结 for-blind-app 开发者自动发现契约。

    测试方法：临时生成能力包，包含内部类和重复名称。
    预期结果：内部类不注册，重复 Tool / Task 名称 fail fast。
    """

    pkg = tmp_path / "capabilities"
    feature = pkg / "demo"
    feature.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (feature / "__init__.py").write_text("", encoding="utf-8")
    (feature / "tool.py").write_text(
        "from audio_chat.tools import BaseTool\n"
        "class DemoTool(BaseTool):\n"
        "    name = 'demo_tool'\n"
        "class _HiddenTool(BaseTool):\n"
        "    name = 'hidden_tool'\n",
        encoding="utf-8",
    )
    (feature / "task.py").write_text(
        "from audio_chat.tasks import BaseTask\n"
        "class DemoTask(BaseTask):\n"
        "    task_type = 'demo_task'\n",
        encoding="utf-8",
    )
    clear_capabilities_modules()
    monkeypatch.syspath_prepend(str(tmp_path))

    tools = ToolAutoDiscovery().discover(["capabilities"], recursive=True)
    tasks = TaskAutoDiscovery().discover(["capabilities"], recursive=True)

    assert [tool.name for tool in tools] == ["demo_tool"]
    assert [task.task_type for task in tasks] == ["demo_task"]

    (feature / "duplicate.py").write_text(
        "from audio_chat.tools import BaseTool\n"
        "from audio_chat.tasks import BaseTask\n"
        "class DuplicateTool(BaseTool):\n"
        "    name = 'demo_tool'\n"
        "class DuplicateTask(BaseTask):\n"
        "    task_type = 'demo_task'\n",
        encoding="utf-8",
    )
    with pytest.raises(ToolError, match="duplicate tool name"):
        ToolAutoDiscovery().discover(["capabilities"], recursive=True)
    with pytest.raises(Exception, match="duplicate task_type"):
        TaskAutoDiscovery().discover(["capabilities"], recursive=True)


def test_example_for_blind_app_files_are_copyable() -> None:
    """测试目标：确认仓库内提供可复制的最小 app-root 示例。

    测试方法：检查示例 README、配置、Tool、Task 和宿主入口文件存在。
    预期结果：功能开发者能从 `app-examples/for-blind-app` 开始复制开发。
    """

    root = Path(__file__).resolve().parents[2] / "app-examples" / "for-blind-app"
    expected = [
        "README.md",
        "server.yaml",
        "templates/capture_photo/tool.py",
        "capabilities/timer/task.py",
        "capabilities/continuous_rgb_analyze/task.py",
        "host/server/main.py",
        "host/phone-mock/config.yaml",
        "host/glass-playback/playback.yaml",
    ]

    missing = [item for item in expected if not (root / item).exists()]

    assert missing == []
