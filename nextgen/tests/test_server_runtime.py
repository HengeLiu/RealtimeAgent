"""服务器运行时与任务承接测试。"""

import pytest

from nextgen.apps.server.runtime.app import ServerRuntimeApp
from nextgen.shared.enums.common import TaskStatus
from nextgen.shared.models.control import DeviceHeartbeat, NodeEndpoint


def test_server_gateway_can_track_ui_state() -> None:
    """验证服务器接入层可以维护 UI partial 和 final 状态。"""

    app = ServerRuntimeApp()
    app.start()

    app.gateway.broadcast_partial_text("帮我找一下水杯")
    app.gateway.broadcast_final_text("开始帮您寻找水杯")
    state = app.gateway.get_ui_state()

    assert state["current_partial"] == "帮我找一下水杯"
    assert state["recent_finals"][-1] == "开始帮您寻找水杯"


def test_create_hybrid_task_creates_session_and_log() -> None:
    """验证混合任务创建技能会创建任务实例并落状态日志。"""

    app = ServerRuntimeApp()
    app.start()

    result = app.create_hybrid_task.run(
        task_name="find_object",
        params={"target_name": "手机"},
    )

    session = app.background_task_center.get_session(result["session_id"])
    snapshot = app.state_log_store.get_task_snapshot(result["session_id"])

    assert result["task_name"] == "find_object"
    assert result["status"] == TaskStatus.STARTING.value
    assert session is not None
    assert session.task_name == "find_object"
    assert snapshot is not None
    assert snapshot["last_event"]["event_name"] == "hybrid_task_created"


def test_event_router_can_dispatch_voice_event_by_keyword() -> None:
    """验证开启关键词分发后，语音事件可以触发任务创建。"""

    app = ServerRuntimeApp()
    app.start()
    app.event_router.enable_keyword_dispatch = True

    result = app.event_router.route(
        {
            "event_type": "voice_event",
            "payload": {"text": "帮我找一下钥匙"},
        }
    )

    dispatch_result = result["dispatch_result"]
    assert dispatch_result["task_name"] == "find_object"
    assert dispatch_result["params"]["target_name"] == "钥匙"


def test_agent_center_can_answer_latest_task_status() -> None:
    """验证智能体中心可以回答最近任务的状态。"""

    app = ServerRuntimeApp()
    app.start()
    created = app.create_hybrid_task.run(
        task_name="find_object",
        params={"target_name": "背包"},
    )

    answer = app.agent_center.answer_task_status(created["session_id"])

    assert "find_object" in answer["answer"]
    assert answer["session"]["session_id"] == created["session_id"]


def test_unknown_device_heartbeat_is_rejected() -> None:
    """验证未注册设备发送心跳时会被拒绝。"""

    app = ServerRuntimeApp()
    app.start()

    with pytest.raises(KeyError, match="设备未注册: phone-001"):
        app.apply_heartbeat(
            DeviceHeartbeat(
                device_id="phone-001",
                status="ready",
                endpoint=NodeEndpoint(host="192.168.1.20", port=19092, scheme="http", base_path="/device-api"),
            )
        )
