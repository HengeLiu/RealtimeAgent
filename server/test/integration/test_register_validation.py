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



def test_register_protocol_mismatch_returns_system_error() -> None:
    settings = Settings(protocol_version="1.0.0")
    logger = create_logger("test-register-validation", "DEBUG")
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
    gateway.open_connection("conn_1", FakeTransport())

    msg = Envelope(
        message_id="msg_register_bad",
        trace_id="trace_register_bad",
        message_type=MessageType.COMMAND,
        message_name="system.register",
        protocol_version="0.9.0",
        source=Endpoint(device_id="dev_glass_001", module="glass-api"),
        target=Endpoint(device_id="dev_server_main", module="server-api"),
        timestamp="2026-04-07T22:00:00+08:00",
        payload={
            "device": {
                "device_id": "dev_glass_001",
                "device_type": "glass",
                "protocol_version": "0.9.0",
                "capabilities": ["audio_input"],
                "status": "registering",
            },
            "auth": {"token": "masked"},
        },
    )

    responses = gateway.receive("conn_1", JsonMessageCodec().encode(msg))
    assert responses[0].message_name == "system.error"
    assert responses[0].payload["error_code"] == "router_error"
