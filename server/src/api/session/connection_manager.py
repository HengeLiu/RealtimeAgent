from __future__ import annotations

from dataclasses import dataclass, field

from api.session.connection_session import ConnectionSession, Transport


@dataclass(slots=True)
class ConnectionManager:
    _by_connection_id: dict[str, ConnectionSession] = field(default_factory=dict)
    _connection_id_by_device: dict[str, str] = field(default_factory=dict)

    def open_session(self, connection_id: str, transport: Transport) -> ConnectionSession:
        session = ConnectionSession(connection_id=connection_id, transport=transport)
        self._by_connection_id[connection_id] = session
        return session

    def bind_device(self, connection_id: str, device_id: str, *, module: str | None = None) -> ConnectionSession:
        session = self._by_connection_id[connection_id]
        old_connection_id = self._connection_id_by_device.get(device_id)
        if old_connection_id and old_connection_id != connection_id:
            old = self._by_connection_id.get(old_connection_id)
            if old:
                old.mark_closed()
                self._by_connection_id.pop(old_connection_id, None)
        session.bind_device(device_id=device_id, module=module)
        self._connection_id_by_device[device_id] = connection_id
        return session

    def get_by_device(self, device_id: str) -> ConnectionSession | None:
        connection_id = self._connection_id_by_device.get(device_id)
        if not connection_id:
            return None
        return self._by_connection_id.get(connection_id)

    def get_by_connection(self, connection_id: str) -> ConnectionSession | None:
        return self._by_connection_id.get(connection_id)

    def mark_heartbeat(self, device_id: str) -> None:
        session = self.get_by_device(device_id)
        if session:
            session.mark_heartbeat()

    def close_session(self, connection_id: str) -> None:
        session = self._by_connection_id.pop(connection_id, None)
        if not session:
            return
        session.mark_closed()
        if session.device_id:
            self._connection_id_by_device.pop(session.device_id, None)

    def prune_stale_sessions(self, timeout_seconds: int) -> list[str]:
        removed: list[str] = []
        for connection_id, session in list(self._by_connection_id.items()):
            if session.is_stale(timeout_seconds):
                removed.append(connection_id)
                self.close_session(connection_id)
        return removed

    def online_device_ids(self) -> list[str]:
        return sorted(self._connection_id_by_device.keys())
