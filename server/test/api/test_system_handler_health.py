from dataclasses import dataclass, field
from datetime import timedelta

from api.handlers.system_handler import SystemHandler
from api.session import ConnectionManager, DeviceRegistry
from infra.config import Settings
from infra.logging import create_logger
from protocol.enums import DeviceStatus, DeviceType
from protocol.models.device import Device


@dataclass
class FakeTransport:
    sent: list[str] = field(default_factory=list)

    def send(self, payload: str) -> None:
        self.sent.append(payload)



def test_system_handler_reconcile_marks_degraded_and_offline() -> None:
    settings = Settings(heartbeat_interval_seconds=1, heartbeat_timeout_seconds=3)
    device_registry = DeviceRegistry()
    manager = ConnectionManager()
    logger = create_logger("test-health", "DEBUG")
    handler = SystemHandler(
        settings=settings,
        device_registry=device_registry,
        connection_manager=manager,
        logger=logger,
    )

    device = Device(
        device_id="dev_glass_001",
        device_type=DeviceType.GLASS,
        protocol_version="1.0.0",
        capabilities=["audio_input"],
        status=DeviceStatus.ONLINE,
    )
    device_registry.upsert(device)
    manager.open_session("conn_1", FakeTransport())
    session = manager.bind_device("conn_1", "dev_glass_001", module="glass-api")

    session.last_heartbeat_at = session.last_heartbeat_at - timedelta(seconds=2)
    handler.reconcile_device_health()
    assert device_registry.get("dev_glass_001").status is DeviceStatus.DEGRADED

    session.last_heartbeat_at = session.last_heartbeat_at - timedelta(seconds=2)
    handler.reconcile_device_health()
    assert device_registry.get("dev_glass_001").status is DeviceStatus.OFFLINE
