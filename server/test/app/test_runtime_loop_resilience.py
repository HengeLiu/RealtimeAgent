from app.container import build_container
from app.runtime_loop import RuntimeLoop
from infra.config import Settings



def test_runtime_loop_survives_task_tick_exception(monkeypatch) -> None:
    container = build_container(Settings(runtime_tick_seconds=0.0, runtime_max_ticks=1))

    def boom(_self):
        raise RuntimeError('task crash')

    monkeypatch.setattr(type(container.task_manager), 'start_next', boom)

    RuntimeLoop(container).run()
