from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
CAPABILITY_ROOT = ROOT / "examples" / "for-blind-app" / "agent-server" / "capabilities"


def _python_files() -> list[Path]:
    return sorted(CAPABILITY_ROOT.glob("*.py"))


def test_for_blind_capability_files_are_flat_and_minimal() -> None:
    """测试目标：确认 for-blind-app 能力目录已经从旧 SDK 多目录样板收敛为扁平文件。

    测试方法：检查 `capabilities` 下只有 `__init__.py`、`tools.py` 和 `tasks.py`。
    预期结果：不会再留下可被自动发现扫描到的旧能力包目录。
    """

    entries = sorted(path.name for path in CAPABILITY_ROOT.iterdir() if path.name != "__pycache__")
    assert entries == ["__init__.py", "tasks.py", "tools.py"]


def test_for_blind_capability_files_are_valid_python_and_use_public_api() -> None:
    """测试目标：确认精简后的能力文件语法正确并只依赖公开扩展面。

    测试方法：用 AST 解析 `tools.py` 和 `tasks.py`，检查 `from realtime_agent import ...` 导入。
    预期结果：业务能力可直接复制，不需要 import SDK 内部服务对象。
    """

    allowed_public_imports = {
        "AssetRef",
        "BaseTask",
        "BaseTool",
        "CommandEvent",
        "CommandHandle",
            "TaskContext",
            "TaskEventView",
            "TaskRunResult",
            "TaskSignal",
            "TaskSpec",
        "ToolContext",
        "ToolError",
        "ToolResult",
        "ToolSpec",
        "VisualAssetRef",
    }
    for path in _python_files():
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported_from_realtime_agent = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "realtime_agent":
                imported_from_realtime_agent.update(alias.name for alias in node.names)
        assert imported_from_realtime_agent
        assert imported_from_realtime_agent <= allowed_public_imports


def test_for_blind_capabilities_do_not_use_hidden_device_rpc_or_device_id_routing() -> None:
    """测试目标：冻结业务能力的设备访问边界。

    测试方法：扫描能力源码，禁止点对点 device_id 路由、隐藏 RPC、WebSocket 直连和
    控制事件内大字节字段。
    预期结果：Tool / Task 只能通过 Context 设备 API 和 Output Service 表达设备能力。
    """

    forbidden_terms = [
        "target_device",
        "target_device_id",
        "source_device_id",
        "send_to_device",
        "send_device",
        "websocket",
        "requests.",
        "httpx.",
        "audio_base64",
        "image_base64",
        "video_base64",
        "payload_bytes",
        "raw_bytes",
    ]
    offenders: list[str] = []
    for path in _python_files():
        text = path.read_text(encoding="utf-8")
        for term in forbidden_terms:
            if term in text:
                offenders.append(f"{path.relative_to(ROOT)}:{term}")
    assert offenders == []


def test_for_blind_capabilities_use_context_device_api() -> None:
    """测试目标：确认当前能力确实通过新版 Context 设备 API 使用设备能力。

    测试方法：检查 Tool 和 Task 中的关键公开调用。
    预期结果：Tool 仍通过 Context 请求单帧 RGB；Task 不再直接播报内部执行状态。
    """

    tools = (CAPABILITY_ROOT / "tools.py").read_text(encoding="utf-8")
    tasks = (CAPABILITY_ROOT / "tasks.py").read_text(encoding="utf-8")

    assert "context.devices.sensors.rgb.one(" in tools
    assert "context.devices.commands.start(" in tasks
    assert "context.output.say(" not in tasks
