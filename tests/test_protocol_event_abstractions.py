from __future__ import annotations

from audio_chat import Event, EventName, EventPattern, StreamType, Subscription
from audio_chat.protocol import CONTROL_EVENTS, STREAM_TYPES


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


def test_subscription_helpers_build_filter_payload() -> None:
    """测试目标：验证订阅辅助方法能生成端侧注册 payload。

    测试方法：用 `EventPattern` 和 `StreamType` 构造按 stream 过滤的订阅。
    预期结果：生成的字典等价于手写 `event + filter`，便于端侧开发者复用。
    """

    subscription = Subscription.for_stream(EventPattern.STREAM_CONTROL_ALL, StreamType.SENSOR_RGB)

    assert subscription.to_dict() == {
        "event": "stream.control.*",
        "filter": {"stream_type": "sensor.rgb"},
    }
