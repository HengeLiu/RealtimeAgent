"""本机测试支持服务测试。"""

from nextgen.integration.test_support.service import build_test_support_app


def test_local_test_support_service_routes_exist() -> None:
    """验证本机测试支持服务具备核心路由。"""

    app = build_test_support_app(server_port=19490, glass_port=19491)
    routes = {route.path for route in app.routes}
    assert "/" in routes
    assert "/health" in routes
    assert "/snapshot" in routes
    assert "/actions/create-find-object-peer-link" in routes
    assert "/actions/send-text" in routes
    assert "/actions/send-image" in routes
    assert "/actions/upload-video" in routes
    assert "/actions/voice/push-to-talk/start" in routes
    assert "/actions/voice/push-to-talk/stop" in routes
    assert "/actions/voice/realtime/start" in routes
    assert "/actions/voice/realtime/stop" in routes
