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
