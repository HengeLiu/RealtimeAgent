import json
from pathlib import Path

import pytest

from audio_chat.protocol import Event, StreamChunk, StreamChunkCodec


CONTRACT_EVENTS = Path(__file__).resolve().parents[1] / "testdata" / "contracts" / "events"


def test_event_envelope_uses_audio_chat_v1_without_target_fields() -> None:
    event = Event(
        event_name="stream.output.open.requested",
        user_id="user-001",
        producer_id="server-main",
        stream_id="stream_out_001",
        stream_type="actuator.speaker",
        payload={"stream_type": "actuator.speaker"},
    )

    data = event.to_dict()

    assert data["version"] == "audio-chat.v1"
    assert data["event_name"] == "stream.output.open.requested"
    assert all(not key.endswith("_device_id") for key in data)


def test_event_rejects_invalid_name_and_point_to_point_fields() -> None:
    """测试目标：冻结公共事件信封，不允许脚本式事件名或点对点设备字段。

    测试方法：构造非法 event_name 和包含 `target_device_id` 的 payload。
    预期结果：序列化时抛出 ValueError，避免业务绕过订阅路由直接指定设备。
    """

    with pytest.raises(ValueError, match="invalid event_name"):
        Event(event_name="Stream.Output", user_id="user-001", producer_id="server-main").to_dict()
    with pytest.raises(ValueError, match="forbidden device routing"):
        Event(
            event_name="control.device.command.requested",
            user_id="user-001",
            producer_id="server-main",
            payload={"target_device_id": "dev-001"},
        ).to_dict()


def test_event_rejects_media_bytes_in_control_payload() -> None:
    """测试目标：确认大媒体数据不能进入控制事件 payload。

    测试方法：分别构造 bytes 字段和常见 base64 媒体字段。
    预期结果：事件序列化抛出 ValueError，提示媒体必须走 stream。
    """

    with pytest.raises(ValueError, match="must not contain bytes"):
        Event(
            event_name="control.device.command.requested",
            user_id="user-001",
            producer_id="server-main",
            payload={"params": {"data": b"\x00\x01"}},
        ).to_dict()
    with pytest.raises(ValueError, match="media bytes"):
        Event(
            event_name="control.device.command.requested",
            user_id="user-001",
            producer_id="server-main",
            payload={"image_base64": "abcd"},
        ).to_dict()


def test_stream_chunk_codec_round_trips_internal_binary_slice() -> None:
    chunk = StreamChunk(
        user_id="user-001",
        session_id="sess-001",
        stream_id="stream_in_001",
        stream_type="sensor.mic",
        seq=7,
        payload=b"abc",
        final=True,
    )

    decoded = StreamChunkCodec.decode(StreamChunkCodec.encode(chunk))

    assert decoded == chunk


def test_protocol_control_golden_events_are_valid() -> None:
    """测试目标：确保 A 线新增控制协议 golden 都符合公共 Event 契约。

    测试方法：逐个读取 `testdata/contracts/events/*.json` 并调用 `Event.from_dict()`。
    预期结果：注册成功、注册失败、订阅命中/未命中和重连 golden 都可被协议解析。
    """

    names = {
        "control_device_register_requested.json",
        "control_device_register_failed.json",
        "control_device_registered.json",
        "subscription_matched.json",
        "subscription_filter_missed.json",
        "device_reconnected.json",
    }

    for name in names:
        event = Event.from_dict(json.loads((CONTRACT_EVENTS / name).read_text(encoding="utf-8")))
        assert event.version == "audio-chat.v1"
        assert event.user_id
        assert event.producer_id
