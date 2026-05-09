from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path


def _load_acceptance_module():
    """加载验收脚本模块，避免测试依赖 scripts 目录成为 Python 包。

    测试方法：用 importlib 直接加载 `scripts/acceptance_check.py`。
    预期结果：测试可以读取 lane 注册表和命令结构。
    """

    audio_chat_root = Path(__file__).resolve().parents[2]
    script_path = audio_chat_root / "scripts" / "acceptance_check.py"
    spec = importlib.util.spec_from_file_location("audio_chat_acceptance_check", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _documented_next_stage_lanes() -> list[str]:
    """从下一阶段计划文档读取 lane 名称清单。

    测试方法：解析“新增 lane 名称”代码块。
    预期结果：文档中的 lane 名称可以和脚本注册表逐项对齐。
    """

    audio_chat_root = Path(__file__).resolve().parents[2]
    document = (audio_chat_root / "docs" / "next-stage-parallel-development-plan.md").read_text(encoding="utf-8")
    match = re.search(r"新增 lane 名称：\n\n```text\n(?P<body>.*?)\n```", document, flags=re.S)
    assert match is not None
    return [line.strip() for line in match.group("body").splitlines() if line.strip()]


def test_next_stage_lanes_are_registered_and_documented() -> None:
    """测试目标：冻结下一阶段 lane 注册表。

    测试方法：对比文档中的 lane 清单和 `NEXT_STAGE_CHECKS`。
    预期结果：后续并行线路可以直接复用统一验收入口，文档和脚本不会漂移。
    """

    acceptance = _load_acceptance_module()

    assert list(acceptance.NEXT_STAGE_CHECKS.keys()) == _documented_next_stage_lanes()
    for lane in acceptance.NEXT_STAGE_CHECKS:
        assert lane in acceptance.CHECKS
        assert acceptance.CHECKS[lane]


def test_foundation_lanes_are_preserved_and_all_includes_next_stage() -> None:
    """测试目标：确认 P0-C 不破坏上一阶段 lane，且 `all` 会覆盖下一阶段 lane。

    测试方法：读取基础 lane 和合并后的 `CHECKS` 注册表。
    预期结果：当前 lane 已进入 `all` 使用的总注册表。
    """

    acceptance = _load_acceptance_module()
    expected_foundation = {
        "p0-foundation",
        "protocol-control",
        "stream-asset",
        "tool-task-agent",
        "output-observability",
        "endpoint-playback",
        "docs-contract",
    }

    assert set(acceptance.FOUNDATION_CHECKS) == expected_foundation
    assert expected_foundation.issubset(acceptance.CHECKS)
    assert set(acceptance.NEXT_STAGE_CHECKS).issubset(acceptance.CHECKS)


def test_next_stage_lane_commands_keep_stable_report_fields() -> None:
    """测试目标：冻结验收报告中每条命令的调试字段。

    测试方法：直接执行一条轻量命令并检查 `run_command` 返回结构。
    预期结果：报告保留 lane、command、cwd、stdout_tail 和 stderr_tail。
    """

    acceptance = _load_acceptance_module()
    command = acceptance.CheckCommand(
        name="report_shape_probe",
        command=(sys.executable, "-c", "print('stdout-probe')"),
        cwd=Path(__file__).resolve().parents[2],
    )

    result = acceptance.run_command("developer-usability", command)

    assert result["ok"] is True
    assert result["lane"] == "developer-usability"
    assert result["command"] == list(command.command)
    assert result["cwd"] == str(command.cwd)
    assert result["stdout_tail"] == ["stdout-probe"]
    assert result["stderr_tail"] == []


def test_documented_acceptance_commands_use_registered_lane_names() -> None:
    """测试目标：确认文档命令里的下一阶段 lane 都已注册。

    测试方法：扫描 `acceptance_check.py <lane>` 命令示例，忽略 `all`、`p0-foundation`
    和占位 `<lane>`。
    预期结果：文档出现的下一阶段 lane 名称全部来自脚本注册表。
    """

    acceptance = _load_acceptance_module()
    audio_chat_root = Path(__file__).resolve().parents[2]
    document = (audio_chat_root / "docs" / "next-stage-parallel-development-plan.md").read_text(encoding="utf-8")
    command_lanes = {
        match.group(1)
        for match in re.finditer(r"acceptance_check\.py[ \t]+([a-z0-9-]+|<lane>)", document)
        if match.group(1) not in {"all", "p0-foundation", "<lane>"}
    }

    assert command_lanes == set(acceptance.NEXT_STAGE_CHECKS)
