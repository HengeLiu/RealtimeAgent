"""运行时集成测试。"""

from nextgen.apps.glass.runtime.app import GlassRuntimeApp
from nextgen.apps.phone.runtime.app import PhoneRuntimeApp
from nextgen.apps.server.runtime.app import ServerRuntimeApp
from nextgen.shared.enums.common import ExecutionType, TaskPriority
from nextgen.shared.models import ExecutionRequest


def test_phone_runtime_can_generate_find_object_hint() -> None:
    """验证手机运行时可以从候选目标直接生成找物提示。"""

    runtime = PhoneRuntimeApp()
    runtime.start()
    candidate = runtime.object_detection_skill.build_object_observation(
        polygon=[(200.0, 100.0), (260.0, 100.0), (260.0, 150.0), (200.0, 150.0)],
        score=0.9,
    )

    hint = runtime.analyze_find_object_frame(
        session_id="tasksess_runtime_001",
        target_name="耳机",
        candidates=[candidate],
    )

    assert "已发现耳机" in hint.text


def test_glass_runtime_can_build_voice_event_and_execute_request() -> None:
    """验证眼镜运行时可以构造语音事件并执行请求。"""

    runtime = GlassRuntimeApp()
    runtime.start()
    event = runtime.build_voice_event("帮我找一下耳机", "audio://001", 0.9)
    feedback = runtime.executor_bus.submit(
        ExecutionRequest(
            execution_id="exec_runtime_001",
            session_id="tasksess_runtime_002",
            execution_type=ExecutionType.SPEECH,
            priority=TaskPriority.HIGH,
            payload={"text": "开始帮您寻找耳机"},
        )
    )

    assert event is not None
    assert event.text == "帮我找一下耳机"
    assert feedback.status == "running"


def test_server_runtime_can_create_and_query_task() -> None:
    """验证服务器运行时可以创建任务并查询状态。"""

    runtime = ServerRuntimeApp()
    runtime.start()
    created = runtime.create_hybrid_task.run("find_object", {"target_name": "雨伞"})
    answer = runtime.agent_center.answer_task_status(created["session_id"])

    assert created["task_name"] == "find_object"
    assert created["session_id"] == answer["session"]["session_id"]
