"""执行总线测试。"""

from nextgen.apps.glass.execution.executor_bus import GlassExecutorBus
from nextgen.shared.enums.common import ExecutionType, TaskPriority
from nextgen.shared.models import ExecutionPolicy, ExecutionRequest


def test_executor_bus_runs_first_request_immediately() -> None:
    """验证执行总线收到第一条请求时会立即进入运行态。"""

    bus = GlassExecutorBus()
    request = ExecutionRequest(
        execution_id="exec_001",
        session_id="tasksess_001",
        execution_type=ExecutionType.SPEECH,
        priority=TaskPriority.NORMAL,
        payload={"text": "开始寻找手机"},
    )

    feedback = bus.submit(request)
    snapshot = bus.get_state_snapshot()

    assert feedback.status == "running"
    assert snapshot["current_request"]["execution_id"] == "exec_001"


def test_executor_bus_can_preempt_interruptible_request() -> None:
    """验证高优先级请求可以抢占可中断的当前请求。"""

    bus = GlassExecutorBus()
    bus.submit(
        ExecutionRequest(
            execution_id="exec_002",
            session_id="tasksess_002",
            execution_type=ExecutionType.SPEECH,
            priority=TaskPriority.NORMAL,
            payload={"text": "继续扫描"},
        )
    )
    feedback = bus.submit(
        ExecutionRequest(
            execution_id="exec_003",
            session_id="tasksess_003",
            execution_type=ExecutionType.BEEP,
            priority=TaskPriority.URGENT,
            payload={"name": "urgent"},
            policy=ExecutionPolicy(interruptible=True),
        )
    )

    assert feedback.status == "preempted_running"
    assert bus.get_state_snapshot()["current_request"]["execution_id"] == "exec_003"
