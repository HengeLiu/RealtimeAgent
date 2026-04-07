from backend_task_core.event_bus import TaskEventBus
from backend_task_core.manager import TaskContextStore, TaskManager
from backend_task_core.registry import TaskRegistry
from backend_task_core.scheduler import TaskScheduler
from backend_task_core.state_machine import TaskStateMachine
from api.handlers.task_handler import TaskHandler
from infra.logging import create_logger
from protocol.enums import MessageType, TaskSource
from protocol.messages.envelope import Endpoint, Envelope
from task.templates.timer_task import TimerTask



def _handler() -> TaskHandler:
    registry = TaskRegistry()
    registry.register(TimerTask.task_type, TimerTask)
    manager = TaskManager(
        task_registry=registry,
        state_machine=TaskStateMachine(),
        scheduler=TaskScheduler(),
        event_bus=TaskEventBus(),
        context_store=TaskContextStore(),
    )
    return TaskHandler(task_manager=manager, logger=create_logger("test-task-handler", "DEBUG"))



def _message(name: str, payload: dict[str, object]) -> Envelope:
    return Envelope(
        message_id="msg_task_1",
        trace_id="trace_task_1",
        message_type=MessageType.COMMAND,
        message_name=name,
        protocol_version="1.0.0",
        source=Endpoint(device_id="dev_server_main", module="agent-core"),
        target=Endpoint(device_id="dev_server_main", module="backend-task-core"),
        timestamp="2026-04-07T22:00:00+08:00",
        payload=payload,
    )



def test_task_handler_create_and_query() -> None:
    handler = _handler()
    created = handler.handle(
        _message(
            "task.create",
            {
                "task_type": "timer",
                "source": TaskSource.AGENT.value,
                "priority": "normal",
                "input": {"duration_seconds": 1},
            },
        )
    )[0]

    task_id = created.payload["task_id"]
    queried = handler.handle(_message("task.query", {"task_id": task_id}))[0]

    assert created.message_name == "task.created"
    assert queried.message_name == "task.state"
    assert queried.payload["task_id"] == task_id



def test_task_handler_pause_invalid_transition_returns_error() -> None:
    handler = _handler()
    created = handler.handle(
        _message(
            "task.create",
            {
                "task_type": "timer",
                "source": TaskSource.AGENT.value,
                "priority": "normal",
                "input": {"duration_seconds": 1},
            },
        )
    )[0]

    task_id = created.payload["task_id"]
    paused = handler.handle(_message("task.pause", {"task_id": task_id}))[0]

    assert paused.message_name == "system.error"
    assert paused.payload["error_code"] == "task_transition_invalid"


def test_task_handler_start_returns_task_completed_when_task_finishes() -> None:
    handler = _handler()
    created = handler.handle(
        _message(
            "task.create",
            {
                "task_type": "timer",
                "source": TaskSource.AGENT.value,
                "priority": "normal",
                "input": {"duration_seconds": 1},
            },
        )
    )[0]

    task_id = created.payload["task_id"]
    started = handler.handle(_message("task.start", {"task_id": task_id}))[0]

    assert started.message_name == "task.completed"
    assert started.payload["task_id"] == task_id


def test_task_handler_start_on_completed_task_returns_task_failed_event() -> None:
    handler = _handler()
    created = handler.handle(
        _message(
            "task.create",
            {
                "task_type": "timer",
                "source": TaskSource.AGENT.value,
                "priority": "normal",
                "input": {"duration_seconds": 1},
            },
        )
    )[0]
    task_id = created.payload["task_id"]

    handler.handle(_message("task.start", {"task_id": task_id}))
    second = handler.handle(_message("task.start", {"task_id": task_id}))[0]

    assert second.message_name == "task.failed"
    assert second.payload["task_id"] == task_id
