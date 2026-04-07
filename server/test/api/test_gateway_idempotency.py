from dataclasses import dataclass, field

from api.gateway import WsGateway
from api.handlers import TaskHandler
from api.router import MessageRouter
from api.session import ConnectionManager
from backend_task_core.event_bus import TaskEventBus
from backend_task_core.manager import TaskContextStore, TaskManager
from backend_task_core.registry import TaskRegistry
from backend_task_core.scheduler import TaskScheduler
from backend_task_core.state_machine import TaskStateMachine
from infra.logging import create_logger
from protocol.codec import JsonMessageCodec
from protocol.enums import MessageType
from protocol.messages.envelope import Endpoint, Envelope
from task.templates.timer_task import TimerTask


@dataclass
class FakeTransport:
    sent: list[str] = field(default_factory=list)

    def send(self, payload: str) -> None:
        self.sent.append(payload)


def test_gateway_deduplicates_task_create_by_message_id() -> None:
    registry = TaskRegistry()
    registry.register(TimerTask.task_type, TimerTask)
    manager = TaskManager(
        task_registry=registry,
        state_machine=TaskStateMachine(),
        scheduler=TaskScheduler(),
        event_bus=TaskEventBus(),
        context_store=TaskContextStore(),
    )
    router = MessageRouter()
    router.register_domain("task", TaskHandler(task_manager=manager, logger=create_logger("test-idempotent", "DEBUG")).handle)
    gateway = WsGateway(router=router, connection_manager=ConnectionManager(), codec=JsonMessageCodec())

    gateway.open_connection("conn_1", FakeTransport())
    envelope = Envelope(
        message_id="msg_task_create_1",
        trace_id="trace_task_1",
        message_type=MessageType.COMMAND,
        message_name="task.create",
        protocol_version="1.0.0",
        source=Endpoint(device_id="dev_server_main", module="agent-core"),
        target=Endpoint(device_id="dev_server_main", module="backend-task-core"),
        timestamp="2026-04-07T22:00:00+08:00",
        payload={"task_type": "timer", "input": {"duration_seconds": 1}},
    )

    raw = JsonMessageCodec().encode(envelope)
    first = gateway.receive("conn_1", raw)
    second = gateway.receive("conn_1", raw)

    assert first[0].message_name == "task.created"
    assert second[0].message_name == "task.created"
    assert first[0].payload["task_id"] == second[0].payload["task_id"]
    assert len(manager.list()) == 1
