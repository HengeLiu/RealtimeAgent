from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = pytest.mark.protocol_spec


ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_DOC = ROOT / "protocol/docs/protocol.md"


def test_protocol_document_exists_and_declares_required_sections() -> None:
    """测试目标：确认标准通讯协议已经落成可观测的正式文档。

    测试方法：读取 `protocol/docs/protocol.md`，检查协议目标、通道、事件信封、
    stream 帧格式、代码映射和变更流程等关键章节。
    预期结果：文档包含后续 L0/L1 测试所需的协议入口信息。
    """

    text = PROTOCOL_DOC.read_text(encoding="utf-8")

    required_markers = [
        "# realtime-agent 通讯协议",
        "## 目标和非目标",
        "## 协议版本",
        "## 通道",
        "## 控制事件信封",
        "## 设备注册",
        "## 能力声明",
        "## 命令生命周期",
        "## 输入 stream 生命周期",
        "## 输出 stream 生命周期",
        "## Stream 二进制帧",
        "## 错误码",
        "## 协议在代码中的映射",
        "## 协议变更流程",
        "## 兼容性策略",
    ]
    for marker in required_markers:
        assert marker in text


def test_protocol_document_maps_protocol_assets_to_code() -> None:
    """测试目标：确认协议文档能反向定位到代码、schema 和 golden fixtures。

    测试方法：检查文档中是否列出协议核心资产和实现路径。
    预期结果：协议变更时开发者能从文档定位到需要同步修改的代码位置。
    """

    text = PROTOCOL_DOC.read_text(encoding="utf-8")
    required_paths = [
        "agent-server/realtime_agent/protocol.py",
        "agent-server/realtime_agent/spec/realtime-agent-device.schema.json",
        "agent-server/realtime_agent/spec/realtime-agent-event.schema.json",
        "agent-server/realtime_agent/spec/realtime-agent-stream.schema.json",
        "agent-server/realtime_agent/spec/realtime-agent-asyncapi.yaml",
        "agent-server/realtime_agent/spec/realtime-agent-error-codes.yaml",
        "agent-server/realtime_agent/control/service.py",
        "agent-server/realtime_agent/stream/service.py",
        "devices/python/src/realtime_agent_device/",
        "devices/typescript/src/",
        "devices/swift/",
        "devices/kotlin/",
        "devices/c/",
        "protocol/data/fixtures/",
    ]
    for path in required_paths:
        assert path in text
        assert (ROOT / path).exists(), path


def test_protocol_document_declares_change_checklist() -> None:
    """测试目标：确认协议变更流程不是口头约定。

    测试方法：检查文档中的协议变更 checklist 是否覆盖文档、schema、fixture、
    Server SDK、Device SDK、L0 和 L1 测试。
    预期结果：后续协议修改必须先更新规范资产，再进入代码实现。
    """

    text = PROTOCOL_DOC.read_text(encoding="utf-8")
    required_items = [
        "先更新本文档的协议语义",
        "同步更新 schema、AsyncAPI 和 error codes",
        "更新 `protocol/data/fixtures` 正例和反例 fixtures",
        "更新 Server SDK 解析、校验和运行时响应",
        "更新 Device SDK 对应语言实现",
        "更新 L0 协议测试",
        "更新 L1 Server / Device SDK contract",
        "在测试报告中记录协议版本、变更点和影响范围",
    ]
    for item in required_items:
        assert item in text
