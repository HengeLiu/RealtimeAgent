"""协议模型测试。"""

from nextgen.shared.enums.common import ChannelType, RuntimeType, TransportMode
from nextgen.shared.models import SourceTargetRef
from nextgen.shared.protocol import DataFrameHeader, Envelope, MessageType, StreamOpenPayload


def test_envelope_to_dict_contains_message_type() -> None:
    """验证统一消息包络可输出关键协议字段。"""

    envelope = Envelope(
        protocol_version="0.1.0",
        message_id="msg_test_001",
        message_type=MessageType.TASK_COMMAND_START,
        channel=ChannelType.CONTROL,
        timestamp="2026-04-02T12:00:00+08:00",
        trace_id="trace_test_001",
        source=SourceTargetRef(runtime=RuntimeType.SERVER, device_id="server-main"),
        target=SourceTargetRef(runtime=RuntimeType.PHONE, device_id="phone-001"),
        session_id="tasksess_test_001",
        requires_ack=True,
        payload={"task_name": "find_object"},
    )
    data = envelope.to_dict()
    assert data["message_type"] == MessageType.TASK_COMMAND_START
    assert data["channel"] == "control"
    assert data["requires_ack"] is True


def test_dataframe_header_to_dict_contains_transport_mode() -> None:
    """验证数据面帧头可输出传输模式。"""

    header = DataFrameHeader(
        protocol_version="0.1.0",
        message_id="msg_frame_001",
        message_type=MessageType.STREAM_FRAME_PUSH,
        channel=ChannelType.DATA,
        transport_mode=TransportMode.PEER,
        session_id="tasksess_test_001",
        source=SourceTargetRef(runtime=RuntimeType.GLASS, device_id="glass-001"),
        target=SourceTargetRef(runtime=RuntimeType.PHONE, device_id="phone-001"),
        payload_meta={"stream_type": "image/jpeg"},
    )
    data = header.to_dict()
    assert data["transport_mode"] == "peer"
    assert data["channel"] == "data"


def test_stream_open_payload_to_dict_contains_direction() -> None:
    """验证开启流载荷可输出方向信息。"""

    payload = StreamOpenPayload(
        stream_id="stream_001",
        transport_mode=TransportMode.RELAY,
        stream_type="image/jpeg",
        direction="glass_to_phone",
    )
    data = payload.to_dict()
    assert data["transport_mode"] == "relay"
    assert data["direction"] == "glass_to_phone"
