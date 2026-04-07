from dataclasses import dataclass, field

from api.session import ConnectionManager


@dataclass
class FakeTransport:
    sent: list[str] = field(default_factory=list)

    def send(self, payload: str) -> None:
        self.sent.append(payload)



def test_connection_manager_reconnect_replaces_old_session() -> None:
    manager = ConnectionManager()
    manager.open_session("conn_1", FakeTransport())
    manager.bind_device("conn_1", "dev_glass_001", module="glass-api")

    manager.open_session("conn_2", FakeTransport())
    manager.bind_device("conn_2", "dev_glass_001", module="glass-api")

    assert manager.get_by_connection("conn_1") is None
    assert manager.get_by_device("dev_glass_001").connection_id == "conn_2"
