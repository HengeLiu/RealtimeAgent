import pytest

from backend_task_core.state_machine import TaskStateMachine
from protocol.enums import TaskStatus



def test_state_machine_allows_valid_transition() -> None:
    sm = TaskStateMachine()
    assert sm.transition(TaskStatus.CREATED, TaskStatus.QUEUED) is TaskStatus.QUEUED



def test_state_machine_rejects_invalid_transition() -> None:
    sm = TaskStateMachine()
    with pytest.raises(ValueError):
        sm.transition(TaskStatus.CREATED, TaskStatus.RUNNING)
