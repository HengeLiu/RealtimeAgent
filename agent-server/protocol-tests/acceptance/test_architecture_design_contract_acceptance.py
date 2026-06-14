from __future__ import annotations

import importlib
import re
from pathlib import Path
import tomllib

from realtime_agent.config import load_yaml_config


def test_public_extension_contract_exports_required_developer_api() -> None:
    """测试目标：确认 SDK 对业务开发者暴露设计文档要求的公开扩展面。

    测试方法：从 `realtime_agent` 顶层包读取 Tool、Task、设备上下文、资产引用和错误对象。
    预期结果：所有公开对象都可以从顶层导入，开发者不需要理解内部服务模块路径。
    """

    import realtime_agent

    required_names = [
        "BaseTool",
        "ToolContext",
        "ToolResult",
        "ToolError",
        "ToolDeviceFacade",
        "BackgroundDeviceFacade",
        "DeviceSnapshot",
        "AssetRef",
        "ArtifactRef",
        "RealtimeAgentError",
        "ErrorCode",
    ]

    missing = [name for name in required_names if not hasattr(realtime_agent, name)]
    assert missing == []


def test_tool_auto_discovery_and_tool_run_contract_exists() -> None:
    """测试目标：确认 Tool 支持按配置自动发现，并具备统一 Tool Run 后台执行内核。

    测试方法：检查自动发现、注册表、策略、schema 构造、执行器、上下文工厂，以及
    Tool Run 状态机、存储和后台 runner 类是否存在。
    预期结果：server 启动不依赖业务开发者在 app.py 中手动注册；长耗时能力由统一 Tool Run
    机制承载（Task 概念已并入 Tool）。
    """

    tools = importlib.import_module("realtime_agent.tools")
    tool_run = importlib.import_module("realtime_agent.tool_run")

    for name in [
        "ToolAutoDiscovery",
        "ToolRegistry",
        "ToolPolicy",
        "ToolSchemaBuilder",
        "ToolGateway",
        "ToolExecutor",
        "ToolContextFactory",
        "ToolRunManagerTool",
        "ToolRunAdmin",
    ]:
        assert hasattr(tools, name), name

    for name in ["ToolRun", "ToolRunStateMachine", "ToolRunStore", "JsonlToolRunStore", "ToolRunRunner"]:
        assert hasattr(tool_run, name), name


def test_tool_run_state_machine_and_follow_up_router_contract() -> None:
    """测试目标：确认 Tool Run 提供状态机和 late result 回流能力。

    测试方法：读取 Tool Run 终态、迁移表，并检查 FollowUpRouter 公开方法。
    预期结果：后台能力状态只能按设计文档声明的路径流转，late result 可注入模型、排队或待通知。
    """

    tool_run = importlib.import_module("realtime_agent.tool_run")
    follow_up = importlib.import_module("realtime_agent.conversation.follow_up")

    assert tool_run.TERMINAL_TOOL_RUN_STATES == {"completed_inline", "followed_up", "failed", "expired", "cancelled"}
    transitions = tool_run.TOOL_RUN_TRANSITIONS
    assert "reported_running" in transitions["running"]
    assert "cancelled" in transitions["running"]
    assert "completed_late" in transitions["reported_running"]
    assert "followed_up" in transitions["completed_late"]

    router = follow_up.FollowUpRouter
    assert hasattr(router, "submit")
    assert hasattr(router, "flush")
    assert hasattr(router, "on_tool_run_complete")


def test_output_service_notification_coordinator_contract_exists() -> None:
    """测试目标：确认 Output Service 包含任务通知协调层。

    测试方法：检查 NotificationCoordinator、NotificationRequest 和 NotificationDecision。
    预期结果：Task / Tool 不直接操作播放队列，而是通过通知协调层进入 Output Router 和 Playback Arbiter。
    """

    output = importlib.import_module("realtime_agent.output")

    for name in ["NotificationCoordinator", "NotificationRequest", "NotificationDecision"]:
        assert hasattr(output, name), name


def test_turn_recorder_and_run_artifact_contract_exists(tmp_path) -> None:
    """测试目标：确认运行产物记录器覆盖新设计要求的关键证据。

    测试方法：实例化 TurnRecorder，检查输入流、转写、模型请求、tool trace、task signal、
    输出流和 result 写入方法。
    预期结果：回放和排障不只依赖日志文本，而能读取稳定 runs 产物。
    """

    observability = importlib.import_module("realtime_agent.observability")
    assert hasattr(observability, "TurnRecorder")

    recorder = observability.TurnRecorder(runs_root=tmp_path / "runs")
    for method_name in [
        "record_input_stream",
        "record_transcript",
        "record_model_request",
        "record_agent_event",
        "record_tool_trace",
        "record_output_stream",
        "write_result",
    ]:
        assert hasattr(recorder, method_name), method_name


def test_yaml_config_matches_design_discovery_and_dev_checks_contract() -> None:
    """测试目标：确认 YAML 配置能表达设计文档中的自动发现和本地验收检查。

    测试方法：读取 examples/simple-agent-server/server.yaml，检查 tools.discover 和 dev_checks。
    预期结果：开发者只需把 BaseTool 子类放到配置包下，并运行 dev_checks 完成验收。
    """

    config = load_yaml_config("examples/simple-agent-server/server.yaml")

    assert hasattr(config, "dev_checks")
    assert config.tools.extra["discover"]["enabled"] is True
    assert isinstance(config.tools.extra["discover"]["packages"], list)
    assert not hasattr(config, "tasks")
    assert config.dev_checks.run_package_check is True
    assert config.dev_checks.run_boundary_check is True


def test_release_gate_docs_and_readme_cli_are_truthful() -> None:
    """测试目标：确认 README 只写真实 CLI，设计文档中未来 CLI 明确标注。

    测试方法：读取 pyproject entry point 并扫描 README / docs 的 realtime-agent 点分命令。
    预期结果：当前入口可执行；未落地入口必须在附近标注后续目标或未落地。
    """

    root = Path(__file__).resolve().parents[3]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = set(pyproject["project"]["scripts"])
    readme_commands = {
        command
        for command in re.findall(r"\brealtime-agent\.[a-z0-9.-]+", (root / "README.md").read_text(encoding="utf-8"))
        if not command.endswith(".yaml")
    }
    assert readme_commands <= scripts

    markers = ("后续目标", "未落地", "建议", "目标", "可选增强", "下一阶段", "应", "未来", )
    prefixes = ("server.", "dev.", "playback.", "config.", "mock.", "web.", "ios.", "esp32.")
    offenders = []
    for path in sorted((root / "docs").glob("*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            for command in re.findall(r"\brealtime-agent\.[a-z0-9.-]+", line):
                if not command.removeprefix("realtime-agent.").startswith(prefixes) or command in scripts:
                    continue
                window = "\n".join(lines[max(0, index - 6) : min(len(lines), index + 3)])
                if not any(marker in window for marker in markers):
                    offenders.append(f"{path.relative_to(root)}:{index + 1}:{command}")

    assert offenders == []
