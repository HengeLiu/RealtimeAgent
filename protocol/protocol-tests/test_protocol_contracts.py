import pytest

from realtime_agent.protocol import Event, StreamChunk, StreamChunkCodec


pytestmark = pytest.mark.protocol


def test_event_envelope_uses_realtime_agent_v1_without_target_fields() -> None:
    event = Event(
        event_name="stream.output.start.requested",
        user_id="user-001",
        producer_id="server-main",
        stream_id="stream_out_001",
        stream_type="actuator.speaker",
        payload={"stream_type": "actuator.speaker"},
    )

    data = event.to_dict()

    assert data["version"] == "realtime-agent.v1"
    assert data["event_name"] == "stream.output.start.requested"
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
            event_name="command.requested",
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
            event_name="command.requested",
            user_id="user-001",
            producer_id="server-main",
            payload={"params": {"data": b"\x00\x01"}},
        ).to_dict()
    with pytest.raises(ValueError, match="media bytes"):
        Event(
            event_name="command.requested",
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
