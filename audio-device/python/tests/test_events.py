import pytest

from audio_chat_device import AudioChatEvent


def test_audio_chat_event_round_trips_json() -> None:
    """测试目标：确认端侧 SDK 的事件信封可 JSON 往返。

    测试方法：构造 `command.completed` 事件，序列化后再解析。
    预期结果：事件名、producer 和 payload 保持一致。
    """

    event = AudioChatEvent(
        event_name="command.completed",
        user_id="user-001",
        producer_id="dev-001",
        payload={"command_id": "cmd-001"},
    )

    decoded = AudioChatEvent.from_json(event.to_json())

    assert decoded.event_name == "command.completed"
    assert decoded.producer_id == "dev-001"
    assert decoded.payload["command_id"] == "cmd-001"


def test_audio_chat_event_rejects_invalid_event_name() -> None:
    """测试目标：确认端侧 SDK 不接受明显非法的事件名格式。

    测试方法：构造包含大写字母的事件名并序列化。
    预期结果：抛出 ValueError，避免发送无效控制事件。
    """

    with pytest.raises(ValueError, match="invalid event_name"):
        AudioChatEvent(event_name="Command.Completed", user_id="user-001", producer_id="dev-001").to_dict()
