from app.container import build_container
from app.runtime_loop import RuntimeLoop
from infra.config import Settings
from protocol.enums import Priority, TaskSource, TaskStatus



def test_runtime_loop_runs_task_tick() -> None:
    settings = Settings(runtime_tick_seconds=0.0, runtime_max_ticks=1)
    container = build_container(settings=settings)

    task = container.task_manager.create_task(
        task_type="timer",
        source=TaskSource.SYSTEM,
        priority=Priority.NORMAL,
        input_data={"duration_seconds": 1},
    )
    assert task.status is TaskStatus.QUEUED

    RuntimeLoop(container).run()

    updated = container.task_manager.get(task.task_id)
    assert updated is not None
    assert updated.status is TaskStatus.COMPLETED
