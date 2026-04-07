from dataclasses import dataclass, field

from api.gateway import WsGateway
from api.handlers import SystemHandler
from api.router import MessageRouter
from api.session import ConnectionManager, DeviceRegistry
from infra.config import Settings
from infra.logging import create_logger
from protocol.codec import JsonMessageCodec
from protocol.enums import MessageType
from protocol.messages.envelope import Endpoint, Envelope


@dataclass
class FakeTransport:
    sent: list[str] = field(default_factory=list)

    def send(self, payload: str) -> None:
        self.sent.append(payload)



def test_register_message_updates_registry_and_returns_registered_event() -> None:
    settings = Settings()
    logger = create_logger("test-register", "DEBUG")
    device_registry = DeviceRegistry()
    connection_manager = ConnectionManager()
    router = MessageRouter()

    system_handler = SystemHandler(
        settings=settings,
        device_registry=device_registry,
        connection_manager=connection_manager,
        logger=logger,
    )
    router.register_domain("system", system_handler.handle)

    gateway = WsGateway(router=router, connection_manager=connection_manager, codec=JsonMessageCodec())

    transport = FakeTransport()
    gateway.open_connection("conn_1", transport)

    register_envelope = Envelope(
        message_id="msg_register_1",
        trace_id="trace_bootstrap_1",
        message_type=MessageType.COMMAND,
        message_name="system.register",
        protocol_version="1.0.0",
        source=Endpoint(device_id="dev_glass_001", module="glass-api"),
        target=Endpoint(device_id="dev_server_main", module="server-api"),
        timestamp="2026-04-07T22:00:00+08:00",
        requires_ack=True,
        payload={
            "device": {
                "device_id": "dev_glass_001",
                "device_type": "glass",
                "protocol_version": "1.0.0",
                "capabilities": ["audio_input"],
                "status": "registering",
            },
            "auth": {"token": "masked"},
        },
    )

    responses = gateway.receive("conn_1", JsonMessageCodec().encode(register_envelope))

    registered_device = device_registry.get("dev_glass_001")
    assert registered_device is not None
    assert registered_device.status.value == "online"

    assert len(responses) == 1
    assert responses[0].message_name == "system.registered"
