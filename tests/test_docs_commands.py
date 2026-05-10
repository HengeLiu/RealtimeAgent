from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import yaml

from audio_chat import AudioChatConfig


AUDIO_ROOT = Path(__file__).resolve().parents[1]


def _project_scripts() -> dict[str, str]:
    data = tomllib.loads((AUDIO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return dict(data["project"]["scripts"])


def test_readme_audio_chat_commands_exist_in_pyproject() -> None:
    """测试目标：防止 README 中的开发命令和 entry point 脱节。

    测试方法：解析 README 中 `uv run audio-chat.*` 命令，排除非 SDK 命令。
    预期结果：每个文档命令都存在于 `pyproject.toml` 的 project.scripts。
    """

    readme = (AUDIO_ROOT / "README.md").read_text(encoding="utf-8")
    commands = sorted(set(re.findall(r"uv run (audio-chat\.[A-Za-z0-9_.-]+)", readme)))
    scripts = _project_scripts()

    assert commands
    assert not [command for command in commands if command not in scripts]


def test_docs_entry_points_exist_in_pyproject() -> None:
    """测试目标：确认设计文档提到的已实现 entry point 没有缺失。

    测试方法：扫描 README 与当前开发说明中出现的 `audio-chat.*` 命令。
    预期结果：非 roadmap 文本中的 P0-A 命令都能在 pyproject 中找到。
    """

    docs = "\n".join(
        [
            (AUDIO_ROOT / "README.md").read_text(encoding="utf-8"),
            (AUDIO_ROOT / "docs" / "device-capability-development-guide.md").read_text(encoding="utf-8"),
        ]
    )
    required = {
        "audio-chat.config.sync",
        "audio-chat.server.start",
        "audio-chat.server.stop",
        "audio-chat.server.logs",
        "audio-chat.web.open",
        "audio-chat.playback.glass",
        "audio-chat.dev.preflight",
        "audio-chat.sdk.package-check",
    }
    mentioned = set(re.findall(r"\b(audio-chat\.[A-Za-z0-9_.-]+)\b", docs))
    scripts = _project_scripts()

    assert required <= mentioned
    assert not [command for command in required if command not in scripts]


def test_readme_public_classes_import_from_audio_chat() -> None:
    """测试目标：防止 README 公开 API 示例失效。

    测试方法：解析 README 中 `from audio_chat import ...` 的导入行并实际导入。
    预期结果：文档中出现的公开类都能从 `audio_chat` 顶层读取。
    """

    readme = (AUDIO_ROOT / "README.md").read_text(encoding="utf-8")
    imported_names: set[str] = set()
    for line in readme.splitlines():
        line = line.strip()
        if not line.startswith("from audio_chat import "):
            continue
        node = ast.parse(line).body[0]
        assert isinstance(node, ast.ImportFrom)
        imported_names.update(alias.name for alias in node.names)

    import audio_chat

    assert imported_names
    assert not [name for name in sorted(imported_names) if not hasattr(audio_chat, name)]


def test_preflight_report_contains_developer_experience_diagnostics(tmp_path) -> None:
    """测试目标：确认 preflight 报告能定位配置、provider 和 endpoint 问题。

    测试方法：执行 `audio-chat.dev.preflight` 到临时报告路径。
    预期结果：报告包含 G 线要求的 config validation、provider key 和 endpoint config 检查。
    """

    report = tmp_path / "preflight.json"
    completed = subprocess.run(
        [
            "uv",
            "run",
            "audio-chat.dev.preflight",
            "--config",
            "app-examples/for-blind-app/server.yaml",
            "--report",
            str(report),
        ],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    names = {check["name"] for check in data["checks"]}
    assert {
        "config_validation",
        "contract_tests",
        "package_import",
        "boundary",
        "recent_playback",
        "provider_keys",
        "provider_runtime_profile",
        "mcp_config",
        "memory_skill",
        "endpoint_config",
    } <= names


def test_for_blind_server_yaml_documents_supported_model_routes(tmp_path) -> None:
    """测试目标：确认 for-blind-app 的精简配置仍能指导开发者启动服务。

    测试方法：读取 `server.yaml`，检查关键 provider 取值和已删除旧 schema/example 引用。
    预期结果：配置可直接作为 app-root 配置使用，不再依赖老 SDK 示例文件。
    """

    config = AUDIO_ROOT / "app-examples" / "for-blind-app" / "server.yaml"
    text = config.read_text(encoding="utf-8")
    data = yaml.safe_load(text)

    assert "server.schema.json" not in text
    assert data["agent"]["mode"] in {"text", "realtime_audio", "auto", "custom"}
    assert data["agent"]["text"]["model_provider"] in {"mock", "openai-compatible", "dashscope-compatible"}
    assert data["agent"]["text"]["model"]
    assert data["agent"]["text"]["asr_provider"] in {"mock", "dashscope"}
    assert data["agent"]["text"]["tts_provider"] in {"mock", "dashscope"}
    assert data["mcp"]["enabled"] is False

    app_dir = tmp_path / "for-blind-app"
    app_dir.mkdir()
    shutil.copyfile(config, app_dir / "server.yaml")
    config = AudioChatConfig.from_yaml(app_dir / "server.yaml")

    assert config.agent_mode == data["agent"]["mode"]
    assert config.text_model_provider == data["agent"]["text"]["model_provider"]
    assert config.text_model == data["agent"]["text"]["model"]
    assert config.asr_provider == data["agent"]["text"]["asr_provider"]
    assert config.tts_provider == data["agent"]["text"]["tts_provider"]
    assert config.allow_mock_fallback == data["agent"]["text"]["allow_mock_fallback"]


def test_developer_context_device_design_doc_covers_target_contracts() -> None:
    """测试目标：确认 Context 与设备 API 设计说明覆盖新版目标契约。

    测试方法：读取指南文档，检查 ToolContext、TaskContext、typed devices API、
    supports.sensors、supports.actuators、selector、AssetRef 和音频通道限制等关键概念。
    预期结果：设计文档清楚标注目标接口，避免被误当成当前实现说明。
    """

    guide = (AUDIO_ROOT / "docs" / "context-device-api-design.md").read_text(encoding="utf-8")
    required = [
        "设计说明",
        "不是当前版本的开发操作手册",
        "ToolContext",
        "TaskContext",
        "context.devices.sensors.rgb.one",
        "context.devices.sensors.rgb.stream",
        "context.devices.commands.call",
        "context.devices.commands.start",
        "context.output.say",
        "supports.sensors[].type",
        "supports.actuators[].type",
        "external",
        "AssetRef",
        "sensors.mic",
        "actuators.speaker",
        "vibrator",
        "selector",
        "创建输入数据流时，如果匹配到多个设备，SDK 应直接抛出错误",
        "ToolContext 中不提供 `tasks`、`memory`、`skills` 这类服务入口",
        "麦克风和喇叭属于系统音频通道",
        "Selector 解析算法",
        "DeviceLease",
        "CommandHandle",
        "DeviceNotFoundError",
        "从当前实现迁移到目标 API",
    ]
    assert not [term for term in required if term not in guide]


def test_device_capability_development_guide_covers_current_workflow() -> None:
    """测试目标：确认当前开发说明覆盖真实可用的设备注册和功能开发入口。

    测试方法：读取开发说明，检查当前 supports 列表、typed facade、ToolDeviceFacade
    API、BaseTool、BaseTask、TaskContext、运行命令和调试产物。
    预期结果：开发者有一份能按当前代码直接操作的说明，并能分清已落地 API 和目标设计。
    """

    guide = (AUDIO_ROOT / "docs" / "device-capability-development-guide.md").read_text(encoding="utf-8")
    required = [
        "当前仓库已经可用",
        "supports",
        "structured supports",
        "context.devices.sensors.rgb.one",
        "context.devices.commands.call",
        "context.output.say",
        "context.devices.sensors.rgb.one",
        "context.devices.commands.call",
        "context.devices.sensors.rgb.stream",
        "context.output.say",
        "BaseTool",
        "BaseTask",
        "ToolSpec",
        "TaskContext",
        "AssetRef | None",
        "audio-chat.device.validate",
        "audio-chat.server.run",
        "audio-chat.web.open",
        "runs/<app_name>/<user_id>/<device_id>/assets.jsonl",
        "当前新 Tool 可以优先试用 typed facade",
    ]
    assert not [term for term in required if term not in guide]
