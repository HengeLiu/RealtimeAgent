from __future__ import annotations

import sys
from pathlib import Path

from realtime_agent.tasks import TaskAutoDiscovery
from realtime_agent.tools import ToolAutoDiscovery


AUDIO_ROOT = Path(__file__).resolve().parents[3]


def test_dev_support_example_exists_with_capability_package(monkeypatch) -> None:
    """测试目标：冻结功能开发者可复制 app-root 的最低门槛。

    测试方法：检查 dev-support 示例服务目录，并用自动发现扫描能力包。
    预期结果：示例服务可以作为空能力包起点，且无需修改 SDK 内部 app.py。
    """

    project_root = AUDIO_ROOT / "examples" / "dev-support"
    app_root = project_root / "agent-server"
    assert (app_root / "server.yaml").exists()
    assert (app_root / "capabilities" / "__init__.py").exists()
    for module_name in list(sys.modules):
        if module_name == "capabilities" or module_name.startswith("capabilities."):
            sys.modules.pop(module_name, None)
    monkeypatch.syspath_prepend(str(app_root))

    tools = ToolAutoDiscovery().discover(["capabilities"], recursive=True)
    tasks = TaskAutoDiscovery().discover(["capabilities"], recursive=True)

    assert {tool.name for tool in tools} == set()
    assert {task.task_type for task in tasks} == set()


def test_device_playback_acceptance_and_artifact_schema_exist() -> None:
    """测试目标：确认开发者闭环包含设备级回放和可检查运行产物说明。

    测试方法：检查 playback 验收测试、配置同步入口和运行产物文档。
    预期结果：后续能力线路不能只提交内部单元测试，必须保留设备级验收入口。
    """

    assert (AUDIO_ROOT / "examples" / "dev-support" / "unit-tests" / "playback" / "test_python_playback.py").exists()
    assert (AUDIO_ROOT / "agent-server" / "realtime_agent" / "cli" / "config.py").exists()
    assert (AUDIO_ROOT / "agent-server" / "docs" / "how-to" / "运行产物排查说明.md").exists()
