from dataclasses import dataclass, field

from api.handlers.peer_handler import PeerHandler
from api.session import BindingRegistry, ConnectionManager
from infra.logging import create_logger
from protocol.enums import BindingStatus, MessageType
from protocol.models.device import DeviceBinding
from protocol.messages.envelope import Endpoint, Envelope


@dataclass
class FakeTransport:
    sent: list[str] = field(default_factory=list)

    def send(self, payload: str) -> None:
        self.sent.append(payload)



def test_peer_prepare_link_returns_ready_when_binding_and_devices_online() -> None:
    binding_registry = BindingRegistry()
    binding_registry.upsert(
        DeviceBinding(
            binding_id="bind_1",
            glass_device_id="dev_glass_001",
            phone_device_id="dev_phone_001",
            status=BindingStatus.ACTIVE,
            created_at="2026-04-07T22:00:00+08:00",
            updated_at="2026-04-07T22:00:00+08:00",
        )
    )

    manager = ConnectionManager()
    manager.open_session("conn_g", FakeTransport())
    manager.bind_device("conn_g", "dev_glass_001", module="glass-api")
    manager.open_session("conn_p", FakeTransport())
    manager.bind_device("conn_p", "dev_phone_001", module="phone-api")

    handler = PeerHandler(
        binding_registry=binding_registry,
        connection_manager=manager,
        logger=create_logger("test-peer", "DEBUG"),
    )

    request = Envelope(
        message_id="msg_peer_1",
        trace_id="trace_peer_1",
        message_type=MessageType.COMMAND,
        message_name="peer.prepare_link",
        protocol_version="1.0.0",
        source=Endpoint(device_id="dev_server_main", module="server-api"),
        target=Endpoint(device_id="dev_glass_001", module="glass-api"),
        timestamp="2026-04-07T22:00:00+08:00",
        payload={"glass_device_id": "dev_glass_001", "phone_device_id": "dev_phone_001", "link_id": "link_1"},
    )

    responses = handler.handle(request)

    assert len(responses) == 1
    assert responses[0].message_name == "peer.link_ready"
    assert responses[0].payload["link_id"] == "link_1"


def test_peer_inbound_event_returns_ack() -> None:
    binding_registry = BindingRegistry()
    manager = ConnectionManager()
    handler = PeerHandler(
        binding_registry=binding_registry,
        connection_manager=manager,
        logger=create_logger("test-peer-event", "DEBUG"),
    )

    event = Envelope(
        message_id="msg_peer_event_1",
        trace_id="trace_peer_event_1",
        message_type=MessageType.EVENT,
        message_name="peer.link_established",
        protocol_version="1.0.0",
        source=Endpoint(device_id="dev_glass_001", module="glass-api"),
        target=Endpoint(device_id="dev_server_main", module="server-api"),
        timestamp="2026-04-07T22:00:00+08:00",
        payload={"link_id": "link_1"},
    )
    response = handler.handle(event)[0]
    assert response.message_name == "peer.ack"
    assert response.message_type is MessageType.ACK
