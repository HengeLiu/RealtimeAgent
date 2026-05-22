from __future__ import annotations

from realtime_agent import Event, EventName, EventPattern, StreamType
from realtime_agent.protocol import CONTROL_EVENTS, STREAM_TYPES


def test_event_name_and_stream_type_enums_are_protocol_strings() -> None:
    """测试目标：验证事件和 stream 枚举可以直接作为协议字符串使用。

    测试方法：用 `EventName` 和 `StreamType` 构造 Event，再转成字典。
    预期结果：输出仍然是标准 JSON 字符串，不暴露 Python 枚举实现细节。
    """

    event = Event(
        event_name=EventName.STREAM_CONTROL_CONFIGURE_REQUESTED,
        user_id="user-001",
        producer_id="server-main",
        stream_type=StreamType.SENSOR_RGB,
        payload={"mode": "single"},
    )

    data = event.to_dict()

    assert data["event_name"] == "stream.control.open.requested"
    assert data["stream_type"] == "sensor.rgb"
    assert data["event_name"] in CONTROL_EVENTS
    assert data["stream_type"] in STREAM_TYPES


def test_event_patterns_are_protocol_strings() -> None:
    """测试目标：验证事件通配模式仍是协议字符串。

    测试方法：读取 `EventPattern` 常量。
    预期结果：常量值可以供 SDK 内部生成路由规则，不暴露额外对象。
    """

    assert str(EventPattern.STREAM_CONTROL_ALL) == "stream.control.*"


def test_rgb_stream_open_payload_accepts_photo_asset_metadata() -> None:
    """测试目标：确认 `stream.control.open.requested` 可携带照片资产策略字段。

    测试方法：构造 sensor.rgb 采集请求，payload 包含 ttl、capture_reason 和 direction。
    预期结果：控制事件信封可正常序列化，且没有新增事件名或媒体 bytes。
    """

    event = Event(
        event_name=EventName.STREAM_CONTROL_OPEN_REQUESTED,
        user_id="user-photo",
        producer_id="server-main",
        stream_type=StreamType.SENSOR_RGB,
        payload={
            "stream_type": "sensor.rgb",
            "mode": "single",
            "format": "jpeg",
            "request_id": "asset_req_001",
            "turn_id": "turn_001",
            "ttl_seconds": 5,
            "capture_reason": "capture_photo",
            "direction": "front",
        },
    )

    data = event.to_dict()

    assert data["event_name"] == "stream.control.open.requested"
    assert data["payload"]["ttl_seconds"] == 5
    assert data["payload"]["direction"] == "front"
