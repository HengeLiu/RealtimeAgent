from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _load_acceptance_module():
    """从文件路径加载验收脚本。

    测试目标：让文档契约测试在仓库根目录和 `audio-chat` 目录下都能运行。
    测试方法：用 importlib 加载 `scripts/acceptance_check.py`。
    预期结果：返回可读取 lane 注册表的模块对象。
    """

    script_path = ROOT / "scripts" / "acceptance_check.py"
    spec = importlib.util.spec_from_file_location("audio_chat_acceptance_docs", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_current_state_docs_do_not_claim_unbacked_old_sdk_parity() -> None:
    """测试目标：防止文档把本阶段后续线路误写成已完成。

    测试方法：检查 README、迁移指南和 for-blind 样板中对 iOS、ESP32、五类老业务能力
    的状态表述，要求使用参考端、可执行样板或后续 lane 口径。
    预期结果：样板能力有验收支撑，但不会被写成真实产品/真机效果已完成。
    """

    docs = "\n".join(
        [
            _read("README.md"),
            _read("docs/audio-chat-sdk-architecture.md"),
            _read("docs/phase3-migration-guide.md"),
            _read("app-examples/for-blind-app/README.md"),
        ]
    )
    for expected in [
        "iOS / ESP32 目录目前是参考端和契约入口",
        "当前已提供可执行 Tool / Task、MCP mock、playback 配置和 `device-api-upgrade-capabilities` lane",
        "tests/acceptance/test_for_blind_capabilities_playback.py",
        "当前为 iOS 参考端目录",
        "当前为 ESP32-S3 参考端目录",
    ]:
        assert expected in docs

    forbidden_patterns = [
        r"find_object.*已完成端到端",
        r"traffic_light.*已完成端到端",
        r"navigation.*已完成端到端",
        r"iOS.*真机.*已完成",
        r"ESP32.*真机.*已完成",
    ]
    offenders = [pattern for pattern in forbidden_patterns if re.search(pattern, docs)]
    assert offenders == []


def test_old_sdk_parity_docs_reference_existing_acceptance_materials() -> None:
    """测试目标：确认文档中的“已实现”口径能落到现有测试、样板或验收 lane。

    测试方法：检查架构文档的验收索引、README 的验收命令和样板目录。
    预期结果：文档状态和可执行材料同步。
    """

    architecture = _read("docs/audio-chat-sdk-architecture.md")
    required_refs = [
        "tests/test_docs_old_sdk_parity.py",
        "tests/acceptance/test_docs_current_state_contract.py",
        "app-examples/for-blind-app",
        "docs/device-api-upgrade-troubleshooting.md",
    ]
    for ref in required_refs:
        assert ref in architecture
        assert (ROOT / ref).exists()

    readme = _read("README.md")
    for lane in [
        "developer-usability",
        "capability-template-playback",
        "device-api-upgrade-capabilities",
        "device-api-upgrade-docs",
    ]:
        assert f"scripts/acceptance_check.py {lane}" in readme


def test_old_sdk_parity_doc_lane_is_registered() -> None:
    """测试目标：确认文档线路已注册到自动验收入口。

    测试方法：导入 `scripts.acceptance_check`，检查 `device-api-upgrade-docs` lane。
    预期结果：本线路能独立执行，也会进入 `all`。
    """

    acceptance_check = _load_acceptance_module()

    assert "device-api-upgrade-docs" in acceptance_check.CHECKS
    commands = acceptance_check.CHECKS["device-api-upgrade-docs"]
    assert commands
    command_text = " ".join(commands[0].command)
    assert "tests/test_docs_old_sdk_parity.py" in command_text
    assert "tests/acceptance/test_docs_current_state_contract.py" in command_text


def test_docs_list_all_for_blind_capability_templates() -> None:
    """测试目标：确认能力样板文档覆盖五类老业务能力。

    测试方法：检查 README、迁移指南和 for-blind 样板 README 都提到五类能力。
    预期结果：迁移入口完整覆盖 find_object、traffic_light、navigation、search、timer。
    """

    docs = "\n".join(
        [
            _read("README.md"),
            _read("docs/phase3-migration-guide.md"),
            _read("app-examples/for-blind-app/README.md"),
        ]
    )
    for capability in ["find_object", "traffic_light", "navigation", "search", "timer"]:
        assert capability in docs
        assert (ROOT / "app-examples" / "for-blind-app" / "capabilities" / capability / "README.md").exists()
