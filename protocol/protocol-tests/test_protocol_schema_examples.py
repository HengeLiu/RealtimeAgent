import json
from pathlib import Path

import pytest

from realtime_agent.device_capabilities import validate_device_capabilities_file
from realtime_agent.protocol import Event, EventName


ROOT = Path(__file__).resolve().parents[2]
SPEC_ROOT = ROOT / "agent-server/realtime_agent/spec"
TESTDATA_ROOT = ROOT / "protocol/data/fixtures"


pytestmark = pytest.mark.protocol


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _schema_event_enum(schema: dict) -> set[str]:
    """读取 schema 中固定标准事件名清单。

    测试目标：兼容 event_name 从纯 enum 调整为 enum + custom.* pattern 后的结构。
    """

    event_schema = schema["properties"]["event_name"]
    if "enum" in event_schema:
        return set(event_schema["enum"])
    for item in event_schema.get("anyOf", []):
        if "enum" in item:
            return set(item["enum"])
    raise AssertionError("event_name schema must contain enum")


def test_event_schema_standard_enum_matches_runtime_event_names() -> None:
    """测试目标：防止运行时标准事件和 JSON schema 清单再次漂移。

    测试方法：比较 `EventName` 枚举值和 schema 中的标准 enum。
    预期结果：schema 覆盖所有标准事件；业务扩展只通过 `custom.*` pattern 放行。
    """

    schema = _load_json(SPEC_ROOT / "realtime-agent-event.schema.json")

    assert _schema_event_enum(schema) == {event.value for event in EventName}


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
    allowed = _schema_event_enum(schema)
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
    allowed = _schema_event_enum(schema)
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
    allowed = _schema_event_enum(schema)
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


def test_downstream_watermark_events_are_standard_protocol_events() -> None:
    """测试目标：确认 speaker buffer 水位线事件属于标准协议事件。

    测试方法：读取事件 schema 的 event_name enum，并分别构造 pause / resume 事件
    交给运行时信封解析。
    预期结果：端侧 SDK 发送的水位线事件不会被 server 按未知事件拒绝。
    """

    schema = _load_json(SPEC_ROOT / "realtime-agent-event.schema.json")
    allowed = _schema_event_enum(schema)
    for name in ["downstream.pause.requested", "downstream.resume.requested"]:
        assert name in allowed
        event = Event.from_dict(
            {
                "version": "realtime-agent.v1",
                "event_id": f"evt_{name.replace('.', '_')}",
                "event_name": name,
                "timestamp_ms": 1,
                "user_id": "user-001",
                "producer_id": "dev-001",
                "session_id": "dev-001",
                "stream_id": "stream-speaker-001",
                "stream_type": "actuator.speaker",
                "payload": {"stream_type": "actuator.speaker", "buffered_ms": 800},
            }
        )
        assert event.event_name == name


def test_custom_events_are_allowed_by_runtime_and_schema_pattern() -> None:
    """测试目标：确认业务扩展事件只能走 `custom.*` 命名空间。

    测试方法：读取 event schema 的 pattern，并分别构造 custom 事件和普通未知事件。
    预期结果：`custom.*` 可被 runtime envelope 接受；非 custom 未知事件仍不进入标准枚举。
    """

    schema = _load_json(SPEC_ROOT / "realtime-agent-event.schema.json")
    event_schema = schema["properties"]["event_name"]
    patterns = [item.get("pattern") for item in event_schema.get("anyOf", []) if item.get("pattern")]
    assert "^custom\\." in "".join(patterns)

    event = Event.from_dict(
        {
            "version": "realtime-agent.v1",
            "event_id": "evt_custom_done",
            "event_name": "custom.haptic.vibrate.done",
            "timestamp_ms": 1,
            "user_id": "user-001",
            "producer_id": "dev-001",
            "payload": {"duration_ms": 120},
        }
    )
    assert event.event_name == "custom.haptic.vibrate.done"
    assert "device.random.event" not in _schema_event_enum(schema)
