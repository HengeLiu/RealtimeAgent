from __future__ import annotations

import sys
from pathlib import Path

import pytest

from realtime_agent.app import RealtimeAgentApp, RealtimeAgentConfig
from realtime_agent.tools import BaseTool, ToolAutoDiscovery, ToolError


def clear_capabilities_modules() -> None:
    """清理测试中复用的 `capabilities` 模块缓存。"""

    for name in list(sys.modules):
        if name == "capabilities" or name.startswith("capabilities."):
            sys.modules.pop(name, None)


def test_example_app_auto_discovery_registers_tool(monkeypatch, tmp_path) -> None:
    """测试目标：确认开发者新增能力后不修改 server 内部代码即可自动注册。

    测试方法：临时创建 app-root 能力包，配置 Tool 递归发现 `capabilities`。
    预期结果：`capture_photo` Tool 进入注册表（Task 已并入 Tool，不再有独立 Task 发现）。
    """

    fixture_root = tmp_path / "example_app"
    capability_root = fixture_root / "capabilities"
    capability_root.mkdir(parents=True)
    (capability_root / "__init__.py").write_text("", encoding="utf-8")
    (capability_root / "tools.py").write_text(
        "from realtime_agent.tools import BaseTool\n"
        "class CapturePhotoTool(BaseTool):\n"
        "    name = 'capture_photo'\n",
        encoding="utf-8",
    )

    clear_capabilities_modules()
    monkeypatch.syspath_prepend(str(fixture_root))
    config = RealtimeAgentConfig(
        runs_root=str(tmp_path / "runs"),
        asset_root=str(tmp_path / "runs" / "assets"),
        tools_discover_enabled=True,
        tools_discover_packages=("capabilities",),
        tools_discover_recursive=True,
    )

    app = RealtimeAgentApp(config)

    assert "capture_photo" in app.tool_registry.list_names()
    assert app.discovery_errors == []


def test_example_app_discovery_contract_skips_internal_and_fails_duplicates(tmp_path, monkeypatch) -> None:
    """测试目标：冻结示例应用开发者自动发现契约。

    测试方法：临时生成能力包，包含内部类和重复名称。
    预期结果：内部类不注册，重复 Tool 名称 fail fast。
    """

    pkg = tmp_path / "capabilities"
    feature = pkg / "demo"
    feature.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (feature / "__init__.py").write_text("", encoding="utf-8")
    (feature / "tool.py").write_text(
        "from realtime_agent.tools import BaseTool\n"
        "class DemoTool(BaseTool):\n"
        "    name = 'demo_tool'\n"
        "class _HiddenTool(BaseTool):\n"
        "    name = 'hidden_tool'\n",
        encoding="utf-8",
    )
    clear_capabilities_modules()
    monkeypatch.syspath_prepend(str(tmp_path))

    tools = ToolAutoDiscovery().discover(["capabilities"], recursive=True)

    assert [tool.name for tool in tools] == ["demo_tool"]

    (feature / "duplicate.py").write_text(
        "from realtime_agent.tools import BaseTool\n"
        "class DuplicateTool(BaseTool):\n"
        "    name = 'demo_tool'\n",
        encoding="utf-8",
    )
    with pytest.raises(ToolError, match="duplicate tool name"):
        ToolAutoDiscovery().discover(["capabilities"], recursive=True)


def test_device_app_demo_files_are_copyable() -> None:
    """测试目标：确认仓库内提供可复制的最小 app-root 示例。

    测试方法：检查 device_app_demo README、配置和 iOS 宿主入口文件存在。
    预期结果：端侧开发者能从 `examples/device_app_demo` 开始复制开发。
    """

    project_root = Path(__file__).resolve().parents[3] / "examples" / "device_app_demo"
    root = project_root / "agent-server"
    assert (project_root / "README.md").exists()
    expected = [
        "server.yaml",
        "../ios/DeviceDemo/DeviceDemoRuntime.swift",
        "../ios/DeviceDemo/ContentView.swift",
    ]

    missing = [item for item in expected if not (root / item).exists()]

    assert missing == []
