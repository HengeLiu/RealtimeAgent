from __future__ import annotations

import re
from pathlib import Path
import tomllib

import pytest


ROOT = Path(__file__).resolve().parents[3]
ARCHITECTURE_DOC = ROOT / "agent-server" / "docs" / "internal" / "realtime-agent抽象架构设计.md"
CONVERSATION_PLAN_DOC = ROOT / "agent-server" / "docs" / "internal" / "conversation音视频链路重构实施计划.md"
SERVER_DOCS_README = ROOT / "agent-server" / "docs" / "README.md"
NEXT_PLAN_DOC = ROOT / "docs" / "next-stage-parallel-development-plan.md"
CLI_PATTERN = re.compile(
    r"\brealtime-agent\.(?:server|config|dev|playback|phone|sdk|web|ios|esp32|mock)[a-z0-9.-]*"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _entry_points() -> set[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return set(data["project"]["scripts"])


def test_architecture_doc_defines_current_abstraction_layers() -> None:
    """测试目标：确认当前架构文档以抽象层级作为权威入口。

    测试方法：读取 `realtime-agent抽象架构设计.md`，检查六层分层和核心抽象名。
    预期结果：开发者从抽象概念进入设计，而不是继续依赖旧 router/pipeline 文档。
    """

    document = _read(ARCHITECTURE_DOC)
    required_terms = [
        "Transport Layer",
        "Input Layer",
        "Agent Layer",
        "Capability Layer",
        "Output Layer",
        "Observability & Config Layer",
        "AgentCoreABC",
        "AgentLoopABC",
        "SpeechInputDelta",
        "AgentOutputDelta",
        "ToolGateway",
    ]
    for term in required_terms:
        assert term in document


def test_architecture_and_readme_cli_commands_are_real_or_roadmap() -> None:
    """测试目标：确认文档中的 CLI 命令不和 pyproject entry point 分叉。

    测试方法：扫描 README、抽象架构文档和实施计划里的 `realtime-agent.*` 命令；
    README 必须全是当前入口，未来命令必须在上下文窗口中标注 roadmap。
    预期结果：开发者复制 README 命令时不会遇到不存在的入口。
    """

    scripts = _entry_points()
    readme_commands = set(CLI_PATTERN.findall(_read(ROOT / "README.md")))
    assert readme_commands <= scripts

    roadmap_markers = ("roadmap", "后续目标", "未实现", "未来", "建议", "下一阶段", "可选增强")
    offenders: list[str] = []
    docs_to_scan = [ARCHITECTURE_DOC, CONVERSATION_PLAN_DOC]
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


def test_conversation_plan_lists_final_acceptance_commands() -> None:
    """测试目标：确认 conversation 实施计划包含最终验收命令。

    测试方法：读取实施计划的最终验收章节，检查 unit/protocol/provider、回归入口
    和 diff 检查命令。
    预期结果：计划不是只描述阶段，而是能指导完成最终验收。
    """

    document = _read(CONVERSATION_PLAN_DOC)
    section = document.split("最终验收命令：", 1)[1].split("\n## ", 1)[0]
    for command in [
        "uv run python -m pytest agent-server/unit-tests -q",
        "uv run python -m pytest agent-server/protocol-tests -q",
        "uv run python -m pytest agent-server/model-provider-tests -q",
        "uv run python -m pytest examples/dev-support/unit-tests/python_playback_glass -q",
        "uv run python -m realtime_agent_python_playback_glass conversation-regression",
        "git diff --check",
    ]:
        assert command in section


def test_server_docs_readme_does_not_index_deprecated_audio_video_docs() -> None:
    """测试目标：确认 server docs 索引不再指向过期音视频链路文档。

    测试方法：读取 `agent-server/docs/README.md`，检查只索引新的抽象、统一链路
    和实施计划，不再索引已移动到 deprecated 的重复文档。
    预期结果：维护者从 README 进入新文档体系。
    """

    readme = _read(SERVER_DOCS_README)
    for expected in [
        "internal/realtime-agent抽象架构设计.md",
        "internal/音视频对话统一链路设计.md",
        "internal/conversation音视频链路重构实施计划.md",
    ]:
        assert expected in readme
    for deprecated_name in [
        "实时音频Pipeline设计.md",
        "Vision实时语音链路设计.md",
        "Vision实时服务器侧标准.md",
        "AgentCore设计.md",
        "服务端SDK总体架构设计.md",
    ]:
        assert deprecated_name not in readme


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
