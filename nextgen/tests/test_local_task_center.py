"""本地任务中心测试。"""

from nextgen.apps.phone.tasks.local_task_center import LocalTaskCenter
from nextgen.shared.enums.common import RuntimeType, TaskPriority, TaskStatus
from nextgen.shared.models import SourceTargetRef, TaskSession


def test_local_task_center_can_transition_session() -> None:
    """验证本地任务中心可以推进任务状态。"""

    center = LocalTaskCenter()
    session = TaskSession(
        session_id="tasksess_local_001",
        task_name="find_object",
        status=TaskStatus.CREATED,
        phase="created",
        priority=TaskPriority.HIGH,
        created_at="2026-04-02T12:00:00+08:00",
        updated_at="2026-04-02T12:00:00+08:00",
        initiator=SourceTargetRef(runtime=RuntimeType.PHONE, device_id="phone-001"),
    )

    center.create_session(session)
    updated = center.transition_session(
        session_id="tasksess_local_001",
        status=TaskStatus.RUNNING,
        phase="guiding",
        summary={"target_name": "水杯"},
    )

    assert updated.status == TaskStatus.RUNNING
    assert updated.phase == "guiding"
    assert center.get_session("tasksess_local_001").last_state_summary["target_name"] == "水杯"
