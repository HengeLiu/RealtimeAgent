from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _project_scripts() -> set[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return set(data["project"]["scripts"])


def test_old_sdk_parity_readme_commands_are_registered() -> None:
    """测试目标：确认 README 中可复制的 `audio-chat.*` 命令都有 entry point。

    测试方法：扫描 README 中 `uv run audio-chat.*` 命令并对比 `pyproject.toml`。
    预期结果：开发者复制 README 命令时不会遇到不存在的 CLI。
    """

    commands = sorted(set(re.findall(r"uv run (audio-chat\.[A-Za-z0-9_.-]+)", _read("README.md"))))
    assert commands
    assert not [command for command in commands if command not in _project_scripts()]


def test_old_sdk_parity_readme_public_api_imports_are_top_level() -> None:
    """测试目标：确认 README 提到的公开 API 都能从 `audio_chat` 顶层导入。

    测试方法：解析 README 的 `from audio_chat import ...` 行并实际检查属性。
    预期结果：文档不会引导业务开发者 import SDK 内部 service。
    """

    imported: set[str] = set()
    for line in _read("README.md").splitlines():
        line = line.strip()
        if not line.startswith("from audio_chat import "):
            continue
        node = ast.parse(line).body[0]
        assert isinstance(node, ast.ImportFrom)
        imported.update(alias.name for alias in node.names)

    import audio_chat

    assert imported
    assert not [name for name in sorted(imported) if not hasattr(audio_chat, name)]


def test_old_sdk_migration_table_covers_main_developer_entries() -> None:
    """测试目标：确认迁移指南覆盖旧 SDK 主要开发者入口。

    测试方法：读取 README 和迁移指南，检查老 SDK 命令、BaseTool/BaseTask、
    DeviceGroupContext、抓拍、通知、MCP、Memory 和 playback config。
    预期结果：老业务开发者能找到等价迁移路径。
    """

    docs = "\n".join([_read("README.md"), _read("docs/phase3-migration-guide.md")])
    required = [
        "openaiglass.config.sync",
        "openaiglass.server.run",
        "openaiglass.phone.mock",
        "openaiglass.glass.start --runtime playback",
        "openaiglass.phone.open",
        "openaiglass.sdk.preflight",
        "BaseTool",
        "BaseTask",
        "DeviceGroupContext",
        "UserDeviceContext",
        "capture_photo",
        "phone video",
        "MCP",
        "Memory",
        "submit_notification",
        "Playback Config",
    ]
    missing = [term for term in required if term not in docs]
    assert missing == []


def test_for_blind_sample_directories_exist_and_are_documented() -> None:
    """测试目标：确认文档提到的老业务能力样板目录真实存在。

    测试方法：检查 `app-examples/for-blind-app` 下五类能力目录和 README。
    预期结果：迁移文档不会引用不存在的样板路径。
    """

    capabilities = ["find_object", "traffic_light", "navigation", "search", "timer"]
    for capability in capabilities:
        path = ROOT / "app-examples" / "for-blind-app" / "capabilities" / capability / "README.md"
        assert path.exists(), capability
        text = path.read_text(encoding="utf-8")
        assert "audio-chat" in text or "迁移路径" in text

    app_readme = _read("app-examples/for-blind-app/README.md")
    for capability in capabilities:
        assert f"capabilities/{capability}" in app_readme


def test_troubleshooting_doc_covers_old_sdk_parity_failure_modes() -> None:
    """测试目标：确认排障文档覆盖老 SDK 对齐阶段的高频失败点。

    测试方法：读取排障文档，检查设备注册、订阅、stream、资产、Tool、Task、
    Output、provider fallback、iOS/ESP32 配置。
    预期结果：功能开发者遇到链路失败时有明确观察点。
    """

    doc = _read("docs/old-sdk-parity-troubleshooting.md")
    required = [
        "设备未注册",
        "订阅未匹配",
        "Stream 未打开",
        "没有资产",
        "Tool 未注册",
        "Task 未恢复",
        "Output 被仲裁丢弃",
        "Provider Fallback",
        "iOS / ESP32 配置不一致",
        "events.jsonl",
        "stream-events.jsonl",
        "assets.jsonl",
        "output-decisions.jsonl",
    ]
    assert not [term for term in required if term not in doc]
