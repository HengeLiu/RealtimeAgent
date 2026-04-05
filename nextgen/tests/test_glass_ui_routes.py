"""眼镜独立 UI 路由测试。"""

from nextgen.apps.glass.runtime.app import GlassRuntimeApp
from nextgen.apps.glass.runtime.http_control_app import build_glass_control_app


def test_glass_ui_routes_exist() -> None:
    """验证眼镜独立 UI 具备核心联调路由。"""

    runtime = GlassRuntimeApp()
    runtime.start()
    runtime.configure_server_base_url("http://127.0.0.1:19490")
    app = build_glass_control_app(runtime)
    routes = {route.path for route in app.routes}
    assert "/" in routes
    assert "/ui/snapshot" in routes
    assert "/ui/actions/create-find-object-peer-link" in routes
    assert "/ui/actions/send-text" in routes
    assert "/ui/actions/send-image" in routes
    assert "/ui/actions/upload-video" in routes
    assert "/ui/actions/voice/push-to-talk/start" in routes
    assert "/ui/actions/voice/push-to-talk/stop" in routes
    assert "/ui/actions/voice/realtime/start" in routes
    assert "/ui/actions/voice/realtime/stop" in routes


def test_glass_ui_snapshot_returns_input_state() -> None:
    """验证眼镜 UI 快照包含感知输入状态。"""

    runtime = GlassRuntimeApp()
    runtime.start()
    payload = runtime.build_ui_snapshot()

    assert "sensor_inputs" in payload
    assert "ui_simulation" in payload["sensor_inputs"]
    assert "registration_state" in payload


def test_glass_ui_snapshot_includes_registration_failure() -> None:
    """验证眼镜 UI 快照会暴露注册失败状态。"""

    runtime = GlassRuntimeApp()
    runtime.start()
    runtime.configure_server_base_url("http://127.0.0.1:19490")
    runtime.mark_registration_failure("register", "connection refused")

    payload = runtime.build_ui_snapshot()

    assert payload["registration_state"]["registered"] is False
    assert payload["registration_state"]["last_action"] == "register"
    assert payload["registration_state"]["last_error"] == "connection refused"
