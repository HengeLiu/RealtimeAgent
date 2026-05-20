import json
from pathlib import Path

import pytest

from realtime_agent.device_capabilities import validate_device_capabilities_file
from realtime_agent.protocol import Event


ROOT = Path(__file__).resolve().parents[2]
SPEC_ROOT = ROOT / "audio-server/realtime_agent/spec"
TESTDATA_ROOT = ROOT / "protocol/data/fixtures"


pytestmark = pytest.mark.protocol


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_device_golden_examples_match_runtime_capability_validator() -> None:
    """测试目标：确认跨语言设备黄金样例能被当前运行时能力校验器接受。

    测试方法：读取 protocol/data/fixtures/devices 下的 JSON 样例，逐个调用
    `validate_device_capabilities_file()`。
    预期结果：浏览器、iOS、ESP32 设备声明都能通过结构化 supports 校验。
    """

    for path in sorted((TESTDATA_ROOT / "devices").glob("*.json")):
        data = _load_json(path)
        supports = validate_device_capabilities_file(data)
        assert supports, path


def test_invalid_device_examples_are_rejected_by_runtime_capability_validator() -> None:
    """测试目标：确认旧版设备能力写法不会被运行时校验器静默接受。

    测试方法：读取 `protocol/data/fixtures/invalid/devices` 下的反例夹具并调用
    `validate_device_capabilities_file()`。
    预期结果：包含旧 `routes/capabilities` 或旧列表型 supports 的设备文件都会抛错。
    """

    for path in sorted((TESTDATA_ROOT / "invalid/devices").glob("*.json")):
        data = _load_json(path)
        with pytest.raises(ValueError):
            validate_device_capabilities_file(data)


def test_event_golden_examples_match_schema_enum_and_runtime_envelope() -> None:
    """测试目标：确认控制事件黄金样例同时符合 schema 事件名清单和运行时信封。

    测试方法：从 `realtime-agent-event.schema.json` 读取 event_name enum，再用
    `Event.from_dict()` 解析每个事件样例。
    预期结果：所有样例事件名都在 schema 中，且运行时可以解析。
    """

    schema = _load_json(SPEC_ROOT / "realtime-agent-event.schema.json")
    allowed = set(schema["properties"]["event_name"]["enum"])
    for path in sorted((TESTDATA_ROOT / "events").glob("*.json")):
        data = _load_json(path)
        assert data["event_name"] in allowed, path
        event = Event.from_dict(data)
        assert event.to_dict()["event_name"] == data["event_name"]


def test_invalid_event_examples_are_rejected_by_schema_or_runtime_envelope() -> None:
    """测试目标：确认协议反例事件能被 schema 枚举或运行时信封校验拦截。

    测试方法：读取 invalid/events 夹具；未知事件名用 schema enum 拦截，点对点路由
    和媒体 payload 用 `Event.from_dict()` 拦截。
    预期结果：每个反例至少被一层协议校验拒绝，不能进入 SDK 行为层。
    """

    schema = _load_json(SPEC_ROOT / "realtime-agent-event.schema.json")
    allowed = set(schema["properties"]["event_name"]["enum"])
    for path in sorted((TESTDATA_ROOT / "invalid/events").glob("*.json")):
        data = _load_json(path)
        if data["event_name"] not in allowed:
            assert path.name == "unknown-event.json"
            continue
        with pytest.raises(ValueError):
            Event.from_dict(data)


def test_event_schema_rejects_unknown_event_name() -> None:
    """测试目标：冻结第一版跨语言 SDK 支持的公共事件名清单。

    测试方法：读取 schema enum，并构造一个格式合法但未纳入协议的事件名。
    预期结果：未知事件名不在 schema enum 中，提醒 SDK 不应把它作为类型化事件暴露。
    """

    schema = _load_json(SPEC_ROOT / "realtime-agent-event.schema.json")
    allowed = set(schema["properties"]["event_name"]["enum"])
    assert "device.random.event" not in allowed
    with pytest.raises(ValueError, match="invalid event_name"):
        Event.from_dict(
            {
                "version": "realtime-agent.v1",
                "event_id": "evt_bad",
                "event_name": "Device.Random",
                "timestamp_ms": 1,
                "user_id": "user-001",
                "producer_id": "dev-001",
                "payload": {},
            }
        )
