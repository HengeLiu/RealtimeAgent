"""真实场景控制面参考实现测试。"""

from nextgen.apps.glass.runtime.app import GlassRuntimeApp
from nextgen.apps.glass.runtime.http_control_app import build_glass_control_app
from nextgen.apps.phone.runtime.app import PhoneRuntimeApp
from nextgen.apps.phone.runtime.http_control_app import build_phone_control_app
from nextgen.apps.server.runtime.app import ServerRuntimeApp
from nextgen.apps.server.runtime.http_control_app import build_server_control_app
from nextgen.shared.enums.common import LinkStatus, RuntimeType
from nextgen.shared.models.base import SourceTargetRef


def test_real_control_plane_can_register_devices_and_render_status_page() -> None:
    """验证服务器控制面可以登记设备并渲染状态页。"""

    runtime = ServerRuntimeApp()
    runtime.start()
    app = build_server_control_app(runtime)
    routes = {route.path for route in app.routes}

    runtime.register_device(
        glass_runtime_registration(
            host="192.168.1.10",
            port=9101,
        )
    )

    page = runtime.render_status_page()
    assert "/devices/register" in routes
    assert "/devices/heartbeat" in routes
    assert "/status" in routes
    assert "glass-001" in page
    assert "nextgen 控制面状态" in page


def test_real_control_plane_can_coordinate_task_scoped_peer_link() -> None:
    """验证真实场景控制面可以完成任务级连接协调主流程。"""

    server_runtime = ServerRuntimeApp()
    server_runtime.start()
    phone_runtime = PhoneRuntimeApp()
    phone_runtime.start()
    phone_runtime.configure_control_endpoint(host="192.168.1.20", port=9200)
    glass_runtime = GlassRuntimeApp(device_id="glass-001")
    glass_runtime.start()
    glass_runtime.configure_control_endpoint(host="192.168.1.10", port=9100)

    server_app = build_server_control_app(server_runtime)
    phone_app = build_phone_control_app(phone_runtime)
    glass_app = build_glass_control_app(glass_runtime)

    server_routes = {route.path for route in server_app.routes}
    phone_routes = {route.path for route in phone_app.routes}
    glass_routes = {route.path for route in glass_app.routes}
    assert "/tasks/{session_id}/peer-link/prepare" in server_routes
    assert "/tasks/create-session" in server_routes
    assert "/tasks/{session_id}/peer-link/orchestrate" in server_routes
    assert "/tasks/{session_id}/peer-link/stop-and-notify" in server_routes
    assert "/device-api/task/prepare-peer-link" in phone_routes
    assert "/device-api/task/connect-peer" in glass_routes

    server_runtime.register_device(glass_runtime_registration(host="192.168.1.10", port=9100))
    server_runtime.register_device(phone_runtime_registration(host="192.168.1.20", port=9200))

    session = server_runtime.background_task_center.create_runtime_session(
        session_id="tasksess_link_001",
        task_name="find_object",
        initiator=SourceTargetRef(runtime=RuntimeType.SERVER, device_id="server-main"),
        input_payload={"target_name": "手机"},
        participants={"glass": ["capture"], "phone": ["local_task"], "server": ["task_center"]},
    )

    prepare_response = server_runtime.prepare_peer_link(
        session_id=session.session_id,
        glass_device_id="glass-001",
        phone_device_id="phone-001",
        stream_type="image_stream",
    )
    phone_command = prepare_response["phone_command"]

    phone_ready = phone_runtime.handle_prepare_peer_link(
        task_session_id=phone_command["task_session_id"],
        peer_device_id=phone_command["peer_device_id"],
        stream_type=phone_command["stream_type"],
    )
    assert phone_ready["status"] == LinkStatus.LISTENING.value

    ready_response = server_runtime.mark_peer_link_ready(
        session_id=session.session_id,
        listen_endpoint=phone_runtime.gateway.control_endpoint.__class__(**phone_ready["listen_endpoint"]),
    )
    glass_command = ready_response["glass_command"]

    glass_connected = glass_runtime.gateway.connect_peer_link(
        task_session_id=glass_command["task_session_id"],
        peer_device_id=glass_command["peer_device_id"],
        peer_endpoint=glass_runtime.gateway.control_endpoint.__class__(**glass_command["peer_endpoint"]),
        stream_type=glass_command["stream_type"],
    )
    assert glass_connected["status"] == LinkStatus.CONNECTED.value

    phone_status = server_runtime.report_peer_link_status(
        session_id=session.session_id,
        runtime="phone",
        status=LinkStatus.CONNECTED,
    )
    glass_status = server_runtime.report_peer_link_status(
        session_id=session.session_id,
        runtime="glass",
        status=LinkStatus.CONNECTED,
    )
    assert phone_status["phone_status"] == LinkStatus.CONNECTED.value
    assert glass_status["status"] == LinkStatus.CONNECTED.value

    stop_response = server_runtime.stop_peer_link(session.session_id)

    phone_stop = phone_runtime.handle_stop_peer_link(stop_response["phone_command"]["task_session_id"])
    glass_stop = glass_runtime.handle_stop_peer_link(stop_response["glass_command"]["task_session_id"])
    assert phone_stop["status"] == LinkStatus.CLOSED.value
    assert glass_stop["status"] == LinkStatus.CLOSED.value

    broken = glass_runtime.build_broken_link_payload(task_session_id=session.session_id, reason="network_lost")
    assert broken["status"] == LinkStatus.BROKEN.value


def glass_runtime_registration(host: str, port: int) -> object:
    """构造眼镜注册对象。"""

    runtime = GlassRuntimeApp(device_id="glass-001")
    runtime.start()
    runtime.configure_control_endpoint(host=host, port=port)
    from nextgen.shared.models.control import DeviceRegistration

    return DeviceRegistration(
        **runtime.gateway.build_registration(
            display_name="眼镜",
            capabilities=[],
        ).__dict__
    )


def phone_runtime_registration(host: str, port: int) -> object:
    """构造手机注册对象。"""

    runtime = PhoneRuntimeApp()
    runtime.start()
    runtime.configure_control_endpoint(host=host, port=port)
    from nextgen.shared.models.control import DeviceRegistration

    return DeviceRegistration(
        **runtime.gateway.build_registration(
            display_name="手机",
            capabilities=[],
        ).__dict__
    )
