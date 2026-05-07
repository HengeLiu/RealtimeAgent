from __future__ import annotations

import sys
from pathlib import Path

from audio_chat.tasks import TaskAutoDiscovery
from audio_chat.tools import ToolAutoDiscovery


AUDIO_ROOT = Path(__file__).resolve().parents[2]


def test_basic_app_example_exists_with_tool_and_task_templates(monkeypatch) -> None:
    """测试目标：冻结功能开发者可复制 app-root 的最低门槛。

    测试方法：检查 `examples/basic-app` 目录，并用自动发现扫描样板能力。
    预期结果：至少有一个 Tool 样板和一个 Task 样板，且无需修改 SDK 内部 app.py。
    """

    app_root = AUDIO_ROOT / "examples" / "basic-app"
    assert (app_root / "README.md").exists()
    assert (app_root / "config" / "server.yaml").exists()
    assert (app_root / "host" / "server" / "main.py").exists()
    for module_name in list(sys.modules):
        if module_name == "capabilities" or module_name.startswith("capabilities."):
            sys.modules.pop(module_name, None)
    monkeypatch.syspath_prepend(str(app_root))

    tools = ToolAutoDiscovery().discover(["capabilities"], recursive=True)
    tasks = TaskAutoDiscovery().discover(["capabilities"], recursive=True)

    assert "capture_photo" in {tool.name for tool in tools}
    assert {"timer", "continuous_rgb_analyze"}.issubset({task.task_type for task in tasks})


def test_device_playback_acceptance_and_artifact_schema_exist() -> None:
    """测试目标：确认开发者闭环包含设备级回放和可检查运行产物。

    测试方法：检查 playback 验收测试、回放配置和运行产物 schema。
    预期结果：后续能力线路不能只提交内部单元测试，必须保留设备级验收入口。
    """

    assert (AUDIO_ROOT / "tests" / "playback" / "test_python_playback.py").exists()
    assert (AUDIO_ROOT / "examples" / "minimal" / "playback.yaml").exists()
    assert (AUDIO_ROOT / "testdata" / "contracts" / "run_artifacts.schema.json").exists()
