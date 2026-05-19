import json
from pathlib import Path

import pytest

from audio_chat_device import AudioChatEvent


ROOT = Path(__file__).resolve().parents[3]
PROTOCOL_ROOT = ROOT / "testdata/protocol"


pytestmark = [pytest.mark.protocol, pytest.mark.device_sdk]


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


def test_python_device_event_reads_protocol_golden_fixtures() -> None:
    """测试目标：确认 Python Device SDK 能消费协议层控制事件黄金样例。

    测试方法：读取 `testdata/protocol/events` 下的所有事件 JSON，并通过
    `AudioChatEvent.from_dict()` 解析后再序列化。
    预期结果：所有标准事件都能保持事件名、用户和 producer 信息。
    """

    for path in sorted((PROTOCOL_ROOT / "events").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        event = AudioChatEvent.from_dict(data)
        encoded = event.to_dict()
        assert encoded["event_name"] == data["event_name"], path
        assert encoded["user_id"] == data["user_id"], path
        assert encoded["producer_id"] == data["producer_id"], path


def test_python_device_event_rejects_invalid_protocol_envelope_fixtures() -> None:
    """测试目标：确认 Python Device SDK 会拒绝非法事件信封。

    测试方法：读取协议反例 fixtures；其中未知事件名属于 schema 层约束，端侧事件对象
    只负责拦截点对点路由字段和媒体 payload。
    预期结果：除未知事件名外的反例都会在 SDK 解析阶段抛出 ValueError。
    """

    for path in sorted((PROTOCOL_ROOT / "invalid/events").glob("*.json")):
        if path.name == "unknown-event.json":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        with pytest.raises(ValueError):
            AudioChatEvent.from_dict(data)
