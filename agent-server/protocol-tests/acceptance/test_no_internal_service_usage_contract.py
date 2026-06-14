from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_ROOTS = [
    ROOT / "examples" / "dev-support" / "agent-server" / "capabilities",
]


def _python_files() -> list[Path]:
    files: list[Path] = []
    for root in EXAMPLE_ROOTS:
        files.extend(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)
    return sorted(files)


def test_examples_and_migration_templates_use_realtime_agent_top_level_api() -> None:
    """测试目标：冻结示例和迁移样板只能依赖 `realtime_agent` 顶层开发者 API。

    测试方法：AST 扫描 dev-support 的示例能力包 Python 文件。
    预期结果：不出现 `realtime_agent.tools`、`realtime_agent.control`、
    `realtime_agent.stream`、`realtime_agent.asset`、`realtime_agent.output` 等内部模块导入。
    """

    forbidden_prefixes = (
        "realtime_agent.tools",
        "realtime_agent.control",
        "realtime_agent.stream",
        "realtime_agent.asset",
        "realtime_agent.output",
        "realtime_agent.protocol",
    )
    offenders: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(forbidden_prefixes):
                        offenders.append(f"{path.relative_to(ROOT)}:{alias.name}")
                continue
            if module.startswith(forbidden_prefixes):
                offenders.append(f"{path.relative_to(ROOT)}:{module}")

    assert offenders == []


def test_examples_do_not_use_hidden_rpc_or_device_id_routing() -> None:
    """测试目标：禁止示例业务代码绕过 `ToolDeviceFacade` 做隐藏通讯。

    测试方法：文本扫描常见点对点发送、服务对象、WebSocket 直连和控制 payload 大字节。
    预期结果：示例只使用 event、stream、asset 和 output 公开 API。
    """

    forbidden_terms = [
        "target_device",
        "target_device_id",
        "source_device_id",
        "send_to_device",
        "send_device",
        "ControlService",
        "StreamService",
        "AssetService",
        "OutputService",
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
