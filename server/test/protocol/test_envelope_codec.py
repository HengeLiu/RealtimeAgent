from protocol.codec import JsonMessageCodec
from protocol.enums import MessageType, Priority
from protocol.messages.envelope import Endpoint, Envelope



def test_envelope_json_codec_roundtrip() -> None:
    codec = JsonMessageCodec()
    envelope = Envelope(
        message_id="msg_1001",
        trace_id="trace_1001",
        message_type=MessageType.COMMAND,
        message_name="system.register",
        protocol_version="1.0.0",
        source=Endpoint(device_id="dev_glass_001", module="glass-api"),
        target=Endpoint(device_id="dev_server_main", module="server-api"),
        timestamp="2026-04-07T22:00:00+08:00",
        payload={"foo": "bar"},
        priority=Priority.HIGH,
        requires_ack=True,
    )

    encoded = codec.encode(envelope)
    decoded = codec.decode(encoded)

    assert decoded.message_id == "msg_1001"
    assert decoded.message_type is MessageType.COMMAND
    assert decoded.priority is Priority.HIGH
    assert decoded.payload == {"foo": "bar"}
