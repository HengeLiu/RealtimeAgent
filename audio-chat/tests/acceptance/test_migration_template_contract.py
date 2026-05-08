from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOT = ROOT / "app-examples" / "for-blind-app" / "templates"


def _python_files() -> list[Path]:
    return sorted(TEMPLATE_ROOT.glob("*/*.py"))


def test_migration_templates_exist_for_stage_h_capabilities() -> None:
    """测试目标：确认 H 线路要求的迁移样板已经存在。

    测试方法：检查 find_object Tool、continuous_rgb_analyze Task 和 notification_task Task
    三类样板文件。
    预期结果：后续业务迁移可以复制样板开始，而不是重新猜 SDK 用法。
    """

    required = [
        TEMPLATE_ROOT / "find_object" / "tool.py",
        TEMPLATE_ROOT / "continuous_rgb_analyze" / "task.py",
        TEMPLATE_ROOT / "notification_task" / "task.py",
        TEMPLATE_ROOT / "README.md",
    ]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.exists()]
    assert missing == []


def test_migration_templates_are_valid_python_and_use_public_api() -> None:
    """测试目标：确认迁移样板语法正确并只依赖公开扩展面。

    测试方法：用 AST 解析样板，检查 `from audio_chat import ...` 导入。
    预期结果：样板可直接复制到业务 app-root，不需要 import SDK 内部服务对象。
    """

    allowed_public_imports = {
        "BaseTask",
        "BaseTool",
        "TaskContext",
        "TaskEvent",
        "ToolContext",
        "ToolResult",
        "ToolSpec",
    }
    assert _python_files()
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported_from_audio_chat = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "audio_chat":
                imported_from_audio_chat.update(alias.name for alias in node.names)
        assert imported_from_audio_chat
        assert imported_from_audio_chat <= allowed_public_imports


def test_migration_templates_do_not_use_hidden_device_rpc_or_device_id_routing() -> None:
    """测试目标：冻结迁移样板的设备访问边界。

    测试方法：扫描样板源码，禁止点对点 device_id 路由、隐藏 RPC、WebSocket 直连和
    控制事件内大字节字段。
    预期结果：Tool / Task 只能通过 UserDeviceContext 的 event、asset、stream 和 output
    方法表达设备能力。
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


def test_migration_templates_use_user_device_context_methods() -> None:
    """测试目标：确认样板确实通过 `context.devices` 使用设备能力。

    测试方法：检查各样板调用的公开设备上下文方法。
    预期结果：find_object 使用资产请求，连续 RGB 使用事件和资产 watch，通知任务使用输出服务。
    """

    find_object = (TEMPLATE_ROOT / "find_object" / "tool.py").read_text(encoding="utf-8")
    continuous = (TEMPLATE_ROOT / "continuous_rgb_analyze" / "task.py").read_text(encoding="utf-8")
    notification = (TEMPLATE_ROOT / "notification_task" / "task.py").read_text(encoding="utf-8")

    assert "context.devices.request_asset(" in find_object
    assert "context.devices.publish_event(" in continuous
    assert "context.devices.watch_assets(" in continuous
    assert "context.devices.submit_text(" in notification


def test_phase3_migration_guide_references_templates_and_constraints() -> None:
    """测试目标：确认迁移指南把样板、边界和验收命令写清楚。

    测试方法：读取 `phase3-migration-guide.md`，检查模板路径、关键约束和 H 线路验收命令。
    预期结果：业务迁移人员可以按指南复制样板并跑独立验收。
    """

    guide = (ROOT / "docs" / "phase3-migration-guide.md").read_text(encoding="utf-8")
    for expected in [
        "app-examples/for-blind-app/templates/find_object/tool.py",
        "app-examples/for-blind-app/templates/continuous_rgb_analyze/task.py",
        "app-examples/for-blind-app/templates/notification_task/task.py",
        "UserDeviceContext",
        "不允许硬编码 device_id",
        "scripts/acceptance_check.py next-docs-contract",
    ]:
        assert expected in guide
