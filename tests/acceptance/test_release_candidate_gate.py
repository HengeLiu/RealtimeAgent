from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _load_acceptance_module():
    """加载验收脚本模块。

    测试目标：避免依赖 scripts 目录是 Python 包。
    测试方法：用 importlib 从文件路径加载 `acceptance_check.py`。
    预期结果：测试可以读取 release lane 注册表。
    """

    script_path = ROOT / "scripts" / "acceptance_check.py"
    spec = importlib.util.spec_from_file_location("audio_chat_acceptance_release", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_release_lane_is_registered_with_gate_steps() -> None:
    """测试目标：确认 J 线发布候选 lane 已进入统一验收入口。

    测试方法：读取 `CHECKS` 中的 `device-api-upgrade-release` 命令。
    预期结果：lane 同时覆盖 release tests、package-check、docs contract、for-blind app
    playback。
    """

    acceptance = _load_acceptance_module()
    assert "device-api-upgrade-release" in acceptance.CHECKS
    commands = acceptance.CHECKS["device-api-upgrade-release"]
    command_text = "\n".join(" ".join(command.command) for command in commands)

    for expected in [
        "tests/test_release_package.py",
        "tests/test_package_boundary.py",
        "tests/acceptance/test_release_candidate_gate.py",
        "audio-chat.sdk.package-check",
        "app-examples/for-blind-app/host/glass-playback/playback.yaml",
    ]:
        assert expected in command_text


def test_release_candidate_docs_and_changelog_are_current() -> None:
    """测试目标：确认发布候选版本、README 和 CHANGELOG 口径一致。

    测试方法：读取 pyproject、README、CHANGELOG 和计划文档。
    预期结果：版本带 rc 标识，文档说明 device-api-upgrade-release 和当前不兼容点。
    """

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    version_match = re.search(r'^version = "([^"]+)"', pyproject, flags=re.M)
    assert version_match is not None
    version = version_match.group(1)
    assert version.endswith("rc1")

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    plan = (ROOT / "docs" / "device-api-upgrade-development-plan.md").read_text(encoding="utf-8")
    for expected in [version, "device-api-upgrade-release", "audio-chat.sdk.package-check"]:
        assert expected in readme
        assert expected in changelog
    for expected in ["当前不兼容点", "ToolDeviceFacade", "event + stream"]:
        assert expected in changelog
    assert "## 14. 并行线路 J：发布候选与包边界" in plan

