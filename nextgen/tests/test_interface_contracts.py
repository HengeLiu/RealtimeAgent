"""接口层测试。"""

from nextgen.apps.server.gateway.server_gateway import ServerGateway
from nextgen.apps.server.runtime.app import ServerRuntimeApp


def test_server_gateway_ui_state_output_shape() -> None:
    """验证服务器接入层 UI 状态输出格式稳定。"""

    gateway = ServerGateway()
    gateway.broadcast_partial_text("测试 partial")
    gateway.broadcast_final_text("测试 final")
    state = gateway.get_ui_state()

    assert sorted(state.keys()) == ["current_partial", "recent_finals"]


def test_server_runtime_keyword_dispatch_output_shape() -> None:
    """验证服务器运行时关键词分发输出格式稳定。"""

    runtime = ServerRuntimeApp()
    runtime.start()
    runtime.event_router.enable_keyword_dispatch = True

    route_result = runtime.event_router.route(
        {"event_type": "voice_event", "payload": {"text": "帮我找一下证件"}}
    )

    assert "dispatch_result" in route_result
    assert "session_id" in route_result["dispatch_result"]
    assert route_result["dispatch_result"]["task_name"] == "find_object"
