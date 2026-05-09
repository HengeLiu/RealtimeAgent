from __future__ import annotations

import importlib
import re
from pathlib import Path
import tomllib

from audio_chat.config import load_yaml_config
from audio_chat.protocol import CONTROL_EVENTS, STREAM_TYPES


def test_public_extension_contract_exports_required_developer_api() -> None:
    """测试目标：确认 SDK 对业务开发者暴露设计文档要求的公开扩展面。

    测试方法：从 `audio_chat` 顶层包读取 Tool、Task、设备上下文、资产引用和错误对象。
    预期结果：所有公开对象都可以从顶层导入，开发者不需要理解内部服务模块路径。
    """

    import audio_chat

    required_names = [
        "BaseTool",
        "ToolContext",
        "ToolResult",
        "ToolError",
        "BaseTask",
        "TaskContext",
        "TaskEvent",
        "TaskRef",
        "ToolDeviceFacade",
        "DeviceSnapshot",
        "AssetRef",
        "ArtifactRef",
        "AudioChatError",
        "ErrorCode",
    ]

    missing = [name for name in required_names if not hasattr(audio_chat, name)]
    assert missing == []


def test_tool_and_task_auto_discovery_contract_exists() -> None:
    """测试目标：确认 Tool / Task 支持按配置自动发现注册。

    测试方法：检查自动发现、注册表、策略、schema 构造、执行器和上下文工厂类是否存在。
    预期结果：server 启动不依赖业务开发者在 app.py 中手动注册 Tool / Task。
    """

    tools = importlib.import_module("audio_chat.tools")
    tasks = importlib.import_module("audio_chat.tasks")

    for name in [
        "ToolAutoDiscovery",
        "ToolRegistry",
        "ToolPolicy",
        "ToolSchemaBuilder",
        "ToolGateway",
        "ToolExecutor",
        "ToolContextFactory",
    ]:
        assert hasattr(tools, name), name

    for name in [
        "BaseTask",
        "TaskAutoDiscovery",
        "TaskRegistry",
        "TaskEngine",
        "TaskStore",
        "TaskStateMachine",
        "TaskExecutor",
        "TaskEventBridge",
    ]:
        assert hasattr(tasks, name), name


def test_task_engine_state_machine_and_event_bridge_contract() -> None:
    """测试目标：确认 Task Engine 按设计文档提供状态机和任务事件回流能力。

    测试方法：读取 Task 状态常量和允许转移表，并检查 TaskEventBridge 公开方法。
    预期结果：长任务状态只能按设计文档声明的路径流转，TaskEvent 可回流消息、通知和 Agent。
    """

    tasks = importlib.import_module("audio_chat.tasks")

    assert set(tasks.TASK_STATES) == {
        "scheduled",
        "running",
        "waiting_external",
        "completed",
        "cancelled",
        "failed",
        "timeout",
    }
    assert ("scheduled", "running") in tasks.TASK_TRANSITIONS
    assert ("running", "waiting_external") in tasks.TASK_TRANSITIONS
    assert ("waiting_external", "completed") in tasks.TASK_TRANSITIONS

    bridge = tasks.TaskEventBridge
    assert hasattr(bridge, "handle_event")
    assert hasattr(bridge, "convert_event_to_agent_turn")


def test_output_service_notification_coordinator_contract_exists() -> None:
    """测试目标：确认 Output Service 包含任务通知协调层。

    测试方法：检查 NotificationCoordinator、NotificationRequest 和 NotificationDecision。
    预期结果：Task / Tool 不直接操作播放队列，而是通过通知协调层进入 Output Router 和 Playback Arbiter。
    """

    output = importlib.import_module("audio_chat.output")

    for name in ["NotificationCoordinator", "NotificationRequest", "NotificationDecision"]:
        assert hasattr(output, name), name


def test_turn_recorder_and_run_artifact_contract_exists(tmp_path) -> None:
    """测试目标：确认运行产物记录器覆盖新设计要求的关键证据。

    测试方法：实例化 TurnRecorder，检查输入流、转写、模型请求、tool trace、task event、
    输出流和 result 写入方法。
    预期结果：回放和排障不只依赖日志文本，而能读取稳定 runs 产物。
    """

    observability = importlib.import_module("audio_chat.observability")
    assert hasattr(observability, "TurnRecorder")

    recorder = observability.TurnRecorder(runs_root=tmp_path / "runs")
    for method_name in [
        "record_input_stream",
        "record_transcript",
        "record_model_request",
        "record_agent_event",
        "record_tool_trace",
        "record_task_event",
        "record_output_stream",
        "write_result",
    ]:
        assert hasattr(recorder, method_name), method_name


def test_yaml_config_matches_design_discovery_and_dev_checks_contract() -> None:
    """测试目标：确认 YAML 配置能表达设计文档中的自动发现和本地验收检查。

    测试方法：读取 app-examples/for-blind-app/server.yaml，检查 tools.discover、tasks.discover 和 dev_checks。
    预期结果：开发者只需把 BaseTool / BaseTask 子类放到配置包下，并运行 dev_checks 完成验收。
    """

    config = load_yaml_config("app-examples/for-blind-app/server.yaml")

    assert hasattr(config, "dev_checks")
    assert config.tools.extra["discover"]["enabled"] is True
    assert isinstance(config.tools.extra["discover"]["packages"], list)
    assert config.tasks.extra["discover"]["enabled"] is True
    assert isinstance(config.tasks.extra["discover"]["packages"], list)
    assert config.dev_checks.run_contract_tests is True
    assert config.dev_checks.run_package_check is True
    assert config.dev_checks.run_boundary_check is True


def test_contract_golden_assets_exist_for_protocol_and_playback() -> None:
    """测试目标：确认公共契约测试资产已经落盘。

    测试方法：检查 events、streams、scenarios 三类 golden 目录和最少一个样例文件。
    预期结果：端侧实现和 server 实现都能围绕 golden 文件做兼容性回归。
    """

    contracts_root = Path(__file__).resolve().parents[2] / "testdata/contracts"
    required_dirs = [
        contracts_root / "events",
        contracts_root / "streams",
        contracts_root / "scenarios",
    ]
    for directory in required_dirs:
        assert directory.is_dir(), str(directory)
        assert any(path.is_file() for path in directory.iterdir()), str(directory)


def test_release_gate_contracts_cover_builtin_events_and_streams() -> None:
    """测试目标：确认每个内置事件和 stream 类型都有契约文件。

    测试方法：按运行时代码中的 `CONTROL_EVENTS` 和 `STREAM_TYPES` 枚举查找 golden JSON。
    预期结果：新增事件或 stream 类型时，必须同步契约样例。
    """

    contracts_root = Path(__file__).resolve().parents[2] / "testdata/contracts"
    missing_events = [
        event_name
        for event_name in sorted(CONTROL_EVENTS)
        if not (contracts_root / "events" / f"{event_name.replace('.', '_')}.json").exists()
    ]
    missing_streams = [
        stream_type
        for stream_type in sorted(STREAM_TYPES)
        if not (contracts_root / "streams" / f"{stream_type.replace('.', '_')}.json").exists()
    ]

    assert missing_events == []
    assert missing_streams == []


def test_release_gate_docs_and_readme_cli_are_truthful() -> None:
    """测试目标：确认 README 只写真实 CLI，设计文档中未来 CLI 明确标注。

    测试方法：读取 pyproject entry point 并扫描 README / docs 的 audio-chat 点分命令。
    预期结果：当前入口可执行；未落地入口必须在附近标注后续目标或未落地。
    """

    root = Path(__file__).resolve().parents[2]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = set(pyproject["project"]["scripts"])
    readme_commands = set(re.findall(r"\baudio-chat\.[a-z0-9.-]+", (root / "README.md").read_text(encoding="utf-8")))
    assert readme_commands <= scripts

    markers = ("后续目标", "未落地", "建议", "目标", "可选增强", "下一阶段", "应", "未来", )
    prefixes = ("server.", "dev.", "playback.", "config.", "mock.", "web.", "ios.", "esp32.")
    offenders = []
    for path in sorted((root / "docs").glob("*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            for command in re.findall(r"\baudio-chat\.[a-z0-9.-]+", line):
                if not command.removeprefix("audio-chat.").startswith(prefixes) or command in scripts:
                    continue
                window = "\n".join(lines[max(0, index - 6) : min(len(lines), index + 3)])
                if not any(marker in window for marker in markers):
                    offenders.append(f"{path.relative_to(root)}:{index + 1}:{command}")

    assert offenders == []
