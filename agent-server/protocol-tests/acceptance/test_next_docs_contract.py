from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path
import tomllib

import pytest


ROOT = Path(__file__).resolve().parents[3]
ARCHITECTURE_DOC = ROOT / "agent-server" / "docs" / "internal" / "agent-server-architecture-design.md"
NEXT_PLAN_DOC = ROOT / "docs" / "next-stage-parallel-development-plan.md"
CLI_PATTERN = re.compile(
    r"\brealtime-agent\.(?:server|config|dev|playback|phone|sdk|web|ios|esp32|mock)[a-z0-9.-]*"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _entry_points() -> set[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return set(data["project"]["scripts"])


def test_architecture_doc_has_current_status_matrix() -> None:
    """测试目标：确认架构文档把已实现、部分实现和未实现能力分开说明。

    测试方法：读取架构文档的状态矩阵，检查核心模块和状态标记。
    预期结果：开发者不会把 roadmap 能力误判成当前可用能力。
    """

    document = _read(ARCHITECTURE_DOC)
    required_terms = [
        "Control Service",
        "Stream Service",
        "Audio Pipeline",
        "Asset Service",
        "Agent Core",
        "Output Service",
        "Task Engine",
        "ToolGateway",
    ]
    for term in required_terms:
        assert term in document

    assert re.search(r"\|\s*Control Service\s*\|\s*已实现\s*\|", document)
    assert re.search(r"\|\s*Stream Service\s*\|\s*已实现\s*\|", document)
    assert re.search(r"\|\s*Audio Pipeline\s*\|\s*部分实现\s*\|", document)
    assert re.search(r"\|\s*Memory / Skill / MCP\s*\|\s*已实现\s*\|", document)


def test_architecture_and_readme_cli_commands_are_real_or_roadmap() -> None:
    """测试目标：确认文档中的 CLI 命令不和 pyproject entry point 分叉。

    测试方法：扫描 README 和架构文档里的 `realtime-agent.*` 命令；README 必须全是
    当前入口，架构文档里的未来命令必须在上下文窗口中标注 roadmap。
    预期结果：开发者复制 README 命令时不会遇到不存在的入口。
    """

    scripts = _entry_points()
    readme_commands = set(CLI_PATTERN.findall(_read(ROOT / "README.md")))
    assert readme_commands <= scripts

    roadmap_markers = ("roadmap", "后续目标", "未实现", "未来", "建议", "下一阶段", "可选增强")
    offenders: list[str] = []
    docs_to_scan = [ARCHITECTURE_DOC]
    if NEXT_PLAN_DOC.exists():
        docs_to_scan.append(NEXT_PLAN_DOC)
    for path in docs_to_scan:
        lines = _read(path).splitlines()
        for index, line in enumerate(lines):
            for command in CLI_PATTERN.findall(line):
                if command in scripts:
                    continue
                window = "\n".join(lines[max(0, index - 5) : min(len(lines), index + 4)])
                if not any(marker in window for marker in roadmap_markers):
                    offenders.append(f"{path.relative_to(ROOT)}:{index + 1}:{command}")
    assert offenders == []


def test_documented_public_classes_are_importable() -> None:
    """测试目标：确认文档列出的公开扩展类可以从 `realtime_agent` 顶层导入。

    测试方法：读取架构文档中的 public API 代码块并逐项 `hasattr(realtime_agent, name)`。
    预期结果：迁移样板和业务开发文档不依赖内部模块路径。
    """

    document = _read(ARCHITECTURE_DOC)
    match = re.search(r"```python\nfrom realtime_agent import (?P<body>.*?)\n```", document, flags=re.S)
    assert match is not None
    names = [
        item.strip()
        for item in match.group("body").replace("\n", " ").split(",")
        if item.strip()
    ]
    realtime_agent = importlib.import_module("realtime_agent")
    missing = [name for name in names if not hasattr(realtime_agent, name)]
    assert missing == []


def test_implemented_status_items_have_acceptance_backing() -> None:
    """测试目标：确认架构文档标记为已实现的核心能力有测试或契约支撑。

    测试方法：读取文档中的“已实现能力验收索引”表，检查每条引用的测试或契约文件存在。
    预期结果：文档状态更新必须和自动验收材料同步。
    """

    document = _read(ARCHITECTURE_DOC)
    section = document.split("### 3.6 已实现能力验收索引", 1)[1].split("\n## ", 1)[0]
    references = re.findall(r"`([^`]*(?:tests|testdata|examples)[^`]*)`", section)
    assert references
    missing = [ref for ref in references if not (ROOT / ref).exists()]
    assert missing == []


def test_next_stage_h_plan_lists_same_contract_categories() -> None:
    """测试目标：确认开发计划 H 章节和契约目录保持一致。

    测试方法：读取 H 章节，检查契约分类名称和实际目录文件对应。
    预期结果：计划不会只写抽象要求，而缺少可验收产物。
    """

    if not NEXT_PLAN_DOC.exists():
        pytest.skip("next-stage parallel development plan is not part of the current document set")
    section = _read(NEXT_PLAN_DOC).split("## 15. 并行线路 H", 1)[1].split("\n## 16.", 1)[0]
    for phrase in ["内置事件 golden", "stream chunk golden", "auth 注册 golden", "task lifecycle golden", "output arbitration golden"]:
        assert phrase in section
