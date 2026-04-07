from backend_task_core.event_bus import TaskEventBus
from backend_task_core.manager import TaskContextStore, TaskManager
from backend_task_core.registry import TaskRegistry
from backend_task_core.scheduler import TaskScheduler
from backend_task_core.state_machine import TaskStateMachine
from protocol.enums import TaskSource
from task.templates.phone_video_link_task import PhoneVideoLinkTask


def test_phone_video_link_task_start_generates_peer_prepare_payload() -> None:
    registry = TaskRegistry()
    registry.register(PhoneVideoLinkTask.task_type, PhoneVideoLinkTask)
    manager = TaskManager(
        task_registry=registry,
        state_machine=TaskStateMachine(),
        scheduler=TaskScheduler(),
        event_bus=TaskEventBus(),
        context_store=TaskContextStore(),
    )
    created = manager.create_task(
        task_type=PhoneVideoLinkTask.task_type,
        source=TaskSource.AGENT,
        input_data={
            "glass_device_id": "dev_glass_001",
            "phone_device_id": "dev_phone_001",
            "transport": "webrtc",
        },
    )

    started = manager.start(created.task_id)

    assert started.result["message_name"] == "peer.prepare_link"
    assert started.result["glass_device_id"] == "dev_glass_001"
    assert started.result["phone_device_id"] == "dev_phone_001"
