from __future__ import annotations

from dataclasses import dataclass

from audio_chat.asset import AssetRef
from audio_chat.output import OutputIntent
from audio_chat.protocol import SERVER_PRODUCER_ID, Event, StreamFormat, new_id


@dataclass(frozen=True)
class DeviceSnapshot:
    device_id: str
    capabilities: dict


class DeviceHandle:
    def __init__(self, snapshot: DeviceSnapshot, *, context: "UserDeviceContext") -> None:
        self.snapshot = snapshot
        self._context = context

    def configure_stream(self, *, stream_type: str, session_id: str | None = None, **request) -> None:
        payload = {"stream_type": stream_type, **request}
        self._context._app.control_service.publish(
            Event(
                event_name="stream.control.configure.requested",
                user_id=self._context.user_id,
                producer_id=SERVER_PRODUCER_ID,
                session_id=session_id,
                stream_type=stream_type,
                payload=payload,
            )
        )

    def open_stream(self, *, stream_type: str, session_id: str | None = None, format: StreamFormat | None = None):
        return self._context._app.open_input_stream(
            user_id=self._context.user_id,
            producer_id=self.snapshot.device_id,
            stream_type=stream_type,
            format=format,
        )

    def start_task(self, *, task_type: str, params: dict | None = None, session_id: str | None = None) -> "EndpointTaskRef":
        task = EndpointTaskRef(task_id=new_id("endpoint_task"), device=self)
        self._context._app.control_service.publish(
            Event(
                event_name="task.state.changed",
                user_id=self._context.user_id,
                producer_id=SERVER_PRODUCER_ID,
                session_id=session_id,
                payload={
                    "task_id": task.task_id,
                    "task_type": task_type,
                    "state": "requested",
                    "params": params or {},
                    "capabilities": self.snapshot.capabilities,
                },
            )
        )
        return task


@dataclass(frozen=True)
class EndpointTaskRef:
    task_id: str
    device: DeviceHandle

    def stop(self, *, reason: str = "cancelled", session_id: str | None = None) -> None:
        context = self.device._context
        context._app.control_service.publish(
            Event(
                event_name="task.state.changed",
                user_id=context.user_id,
                producer_id=SERVER_PRODUCER_ID,
                session_id=session_id,
                payload={"task_id": self.task_id, "state": "cancel_requested", "reason": reason},
            )
        )


class UserDeviceContext:
    def __init__(self, *, user_id: str, app) -> None:
        self.user_id = user_id
        self._app = app

    def get_devices(self, capability: str | None = None) -> list[DeviceSnapshot]:
        devices = []
        for record in self._app.control_service.get_active_device_set(self.user_id).devices:
            if capability is None or self._has_capability(record.capabilities, capability):
                devices.append(DeviceSnapshot(device_id=record.device_id, capabilities=record.capabilities))
        return devices

    def find_device(self, capability: str) -> DeviceHandle | None:
        devices = self.get_devices(capability)
        return DeviceHandle(devices[0], context=self) if devices else None

    def get_or_request_asset(self, *, stream_type: str, session_id: str | None = None) -> AssetRef | None:
        return self._app.get_or_request_asset(user_id=self.user_id, stream_type=stream_type, session_id=session_id)

    def submit_output(self, intent: OutputIntent, text: str) -> None:
        self._app.output_service.submit_output(intent, text)

    @staticmethod
    def _has_capability(capabilities: dict, capability: str) -> bool:
        if capabilities.get(capability):
            return True
        return capability in capabilities.get("streams.produce", []) or capability in capabilities.get("streams.consume", [])


class GetOrRequestAssetTool:
    name = "get_or_request_asset"

    def run(self, context: UserDeviceContext, *, stream_type: str, session_id: str | None = None) -> AssetRef | None:
        return context.get_or_request_asset(stream_type=stream_type, session_id=session_id)
