from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from audio_chat.config import load_yaml_config
from audio_chat.errors import ErrorCode
from audio_chat.tasks import BaseTask, TaskAutoDiscovery, TaskEvent, TaskRegistry
from audio_chat.tools import BaseTool, ToolAutoDiscovery, ToolError, ToolRegistry, ToolResult


def test_tool_result_public_contract_fields() -> None:
    """测试目标：冻结 ToolResult 的 P0 公开字段和成功/失败工厂。

    测试方法：分别构造成功和失败结果，检查字段名、默认值和结构化错误。
    预期结果：Agent Core 和业务 Tool 可以稳定读取 ok/data/message/assets/artifacts/tasks/meta/error。
    """

    success = ToolResult.success(data={"answer": 1}, message="done", meta={"trace_id": "trace-1"})
    failure = ToolResult.failed(
        ToolError(
            "bad input",
            code=ErrorCode.INVALID_ARGUMENT,
            retryable=False,
            details={"field": "name"},
        )
    )

    assert success.ok is True
    assert success.data == {"answer": 1}
    assert success.message == "done"
    assert success.assets == []
    assert success.artifacts == []
    assert success.tasks == []
    assert success.meta == {"trace_id": "trace-1"}
    assert success.error is None
    assert success.content == success.data
    assert success.metadata == success.meta

    assert failure.ok is False
    assert failure.error == {
        "code": "invalid_argument",
        "message": "bad input",
        "retryable": False,
        "details": {"field": "name"},
    }


def test_task_event_public_decision_and_notify_flags() -> None:
    """测试目标：冻结 TaskEvent 的 Agent 决策和直达通知开关。

    测试方法：构造默认事件和需要 Agent 决策的事件。
    预期结果：默认允许直接通知，复杂事件可显式要求 Agent 决策。
    """

    default_event = TaskEvent(
        task_id="task-1",
        task_type="timer",
        event_name="task.completed",
        user_id="user-1",
    )
    decision_event = TaskEvent(
        task_id="task-2",
        task_type="navigation",
        event_name="navigation.decision.required",
        user_id="user-1",
        requires_agent_decision=True,
        allow_direct_notify=False,
    )

    assert default_event.requires_agent_decision is False
    assert default_event.allow_direct_notify is True
    assert decision_event.requires_agent_decision is True
    assert decision_event.allow_direct_notify is False


def test_discovery_config_and_dev_checks_fields_are_loaded() -> None:
    """测试目标：确认配置支持 P0 自动发现和 dev_checks 新字段。

    测试方法：读取最小配置，检查 recursive、fail_fast、report_path 和
    require_recent_playback_ok。
    预期结果：后续并行线路可以直接依赖这些配置字段。
    """

    config = load_yaml_config("app-examples/for-blind-app/server.yaml")

    assert config.tools.discover.recursive is True
    assert config.tools.discover.fail_fast is True
    assert config.tasks.discover.recursive is True
    assert config.tasks.discover.fail_fast is True
    assert config.dev_checks.report_path == "runs/audio-chat/for-blind-app/preflight.json"
    assert config.dev_checks.require_recent_playback_ok is False


def test_auto_discovery_recurses_skips_internal_and_fails_on_duplicate(tmp_path, monkeypatch) -> None:
    """测试目标：验证 Tool / Task 自动发现的 P0 行为。

    测试方法：临时创建嵌套包，包含根模块、子模块、内部类和重复名称。
    预期结果：递归扫描能发现子模块类，内部类被跳过，重复名称会 fail fast。
    """

    pkg = tmp_path / "demo_pkg"
    sub = pkg / "nested"
    sub.mkdir(parents=True)
    (pkg / "__init__.py").write_text(
        "from audio_chat.tools import BaseTool\n"
        "from audio_chat.tasks import BaseTask\n"
        "class RootTool(BaseTool):\n"
        "    name = 'root_tool'\n"
        "class RootTask(BaseTask):\n"
        "    task_type = 'root_task'\n"
        "class _InternalTool(BaseTool):\n"
        "    name = 'internal_tool'\n",
        encoding="utf-8",
    )
    (sub / "__init__.py").write_text("", encoding="utf-8")
    (sub / "feature.py").write_text(
        "from audio_chat.tools import BaseTool\n"
        "from audio_chat.tasks import BaseTask\n"
        "class NestedTool(BaseTool):\n"
        "    name = 'nested_tool'\n"
        "class NestedTask(BaseTask):\n"
        "    task_type = 'nested_task'\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    tools = ToolAutoDiscovery().discover(["demo_pkg"], recursive=True)
    tasks = TaskAutoDiscovery().discover(["demo_pkg"], recursive=True)

    assert {tool.name for tool in tools} == {"root_tool", "nested_tool"}
    assert {task.task_type for task in tasks} == {"root_task", "nested_task"}

    (sub / "duplicate.py").write_text(
        "from audio_chat.tools import BaseTool\n"
        "from audio_chat.tasks import BaseTask\n"
        "class DuplicateTool(BaseTool):\n"
        "    name = 'nested_tool'\n"
        "class DuplicateTask(BaseTask):\n"
        "    task_type = 'nested_task'\n",
        encoding="utf-8",
    )
    with pytest.raises(ToolError, match="duplicate tool name"):
        ToolAutoDiscovery().discover(["demo_pkg"], recursive=True)
    with pytest.raises(Exception, match="duplicate task_type"):
        TaskAutoDiscovery().discover(["demo_pkg"], recursive=True)


def test_discovery_records_import_errors_when_not_fail_fast(tmp_path, monkeypatch) -> None:
    """测试目标：确认自动发现可按配置记录导入失败而不中止。

    测试方法：临时创建一个子模块导入即失败的包，设置 fail_fast=False。
    预期结果：发现器返回可用类，并把失败模块写入 errors。
    """

    pkg = tmp_path / "broken_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "from audio_chat.tools import BaseTool\n"
        "class WorkingTool(BaseTool):\n"
        "    name = 'working_tool'\n",
        encoding="utf-8",
    )
    (pkg / "broken.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    discovery = ToolAutoDiscovery()
    tools = discovery.discover(["broken_pkg"], recursive=True, fail_fast=False)

    assert [tool.name for tool in tools] == ["working_tool"]
    assert discovery.errors
    assert discovery.errors[0]["module"] == "broken_pkg.broken"


def test_registry_duplicate_names_fail_fast() -> None:
    """测试目标：确认注册表自身也拒绝重复 Tool / Task 名称。

    测试方法：注册两个同名 Tool 和两个同 task_type Task。
    预期结果：第二次注册抛出结构化协议错误。
    """

    class FirstTool(BaseTool):
        name = "same_tool"

    class SecondTool(BaseTool):
        name = "same_tool"

    class FirstTask(BaseTask):
        task_type = "same_task"

    class SecondTask(BaseTask):
        task_type = "same_task"

    tool_registry = ToolRegistry()
    task_registry = TaskRegistry()
    tool_registry.register(FirstTool())
    task_registry.register(FirstTask)

    with pytest.raises(ToolError, match="duplicate tool name"):
        tool_registry.register(SecondTool())
    with pytest.raises(Exception, match="duplicate task_type"):
        task_registry.register(SecondTask)


def test_preflight_generates_p0_json_report(tmp_path) -> None:
    """测试目标：确认 preflight 聚合检查能生成 P0 JSON 报告。

    测试方法：运行 `audio_chat.preflight` 模块，读取输出 JSON。
    预期结果：报告状态为 ok，包含 contract、package、boundary 和降级说明。
    """

    report = tmp_path / "preflight.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "audio_chat.preflight",
            "--config",
            "app-examples/for-blind-app/server.yaml",
            "--report",
            str(report),
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    data = __import__("json").loads(report.read_text(encoding="utf-8"))
    assert data["status"] == "ok"
    assert {check["name"] for check in data["checks"]} >= {
        "contract_tests",
        "package_import",
        "boundary",
        "live_server",
        "recent_playback",
    }
    assert data["not_implemented"]["audio_pipeline.vad"]
