from __future__ import annotations

import ast
import json
import re
import subprocess
import tomllib
from pathlib import Path


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

    测试方法：扫描 README 与下一阶段计划中出现的 `audio-chat.*` 命令。
    预期结果：非 roadmap 文本中的 P0-A 命令都能在 pyproject 中找到。
    """

    docs = "\n".join(
        [
            (AUDIO_ROOT / "README.md").read_text(encoding="utf-8"),
            (AUDIO_ROOT / "docs" / "next-stage-parallel-development-plan.md").read_text(encoding="utf-8"),
        ]
    )
    required = {
        "audio-chat.config.sync",
        "audio-chat.server.start",
        "audio-chat.server.stop",
        "audio-chat.server.logs",
        "audio-chat.phone.mock",
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
            "app-examples/basic-app/server.yaml",
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
