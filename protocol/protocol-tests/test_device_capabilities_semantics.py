from __future__ import annotations

import json
from pathlib import Path

import pytest

from realtime_agent import RealtimeAgentApp, RealtimeAgentConfig, Event, StreamChunk
from realtime_agent.device_capabilities import (
    compile_device_capabilities_file,
    compile_internal_routes_from_supports,
    compile_registration_payload,
    compile_system_routes_from_properties,
)


class FakeConnection:
    """测试用端侧连接。

    主要功能：记录 server 按订阅投递给设备的事件，避免测试依赖真实 WebSocket。
    """

    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.events: list[Event] = []

    def push_event(self, event: Event) -> None:
        self.events.append(event)

    def push_stream_chunk(self, chunk: object) -> None:
        pass

    def close(self, *, reason: str) -> None:
        pass


def test_browser_device_capability_file_compiles_to_routes() -> None:
    """测试目标：验证浏览器设备能力文件能编译成协议订阅。

    测试方法：读取 `device.realtime-agent.yaml`，检查结构化能力和编译产物。
    预期结果：设备开发者不需要手写 routes，也能得到 RGB 和 haptic 订阅。
    """

    result = compile_device_capabilities_file("examples/dev-support/devices/browser-glass/device.realtime-agent.yaml")

    assert set(result["payload"]["supports"]) == {"sensors", "actuators"}
    routes = compile_internal_routes_from_supports(result["payload"]["supports"])
    assert {"event": "control.audio_session.*"} in routes
    assert {"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}} in routes
    assert {"event": "stream.output.*", "filter": {"stream_type": "actuator.haptic"}} in routes
    assert {"event": "command.*", "filter": {"payload.command": "haptic.vibrate"}} in routes


def test_unknown_support_id_fails_fast() -> None:
    """测试目标：验证语义 ID 写错时能被校验拦住。

    测试方法：构造 `sensor.rbg` 拼写错误。
    预期结果：编译阶段抛出 ValueError，避免端侧带着错误订阅启动。
    """

    with pytest.raises(ValueError, match="unknown support id"):
        compile_internal_routes_from_supports({"sensors": [{"type": "rbg"}]})


def test_structured_supports_compile_to_routes(tmp_path: Path) -> None:
    """测试目标：验证新版结构化能力声明能被 server 编译成当前路由订阅。

    测试方法：构造 `supports.sensors[].type` 和 `supports.actuators[].type` 写法，
    读取编译结果中的标准 support id、默认参数映射和订阅。
    预期结果：设备开发者可以按传感器/执行器语义写配置，server 内部仍得到稳定的
    `sensor.*` / `actuator.*` 能力和订阅。
    """

    capability_file = tmp_path / "device.realtime-agent.yaml"
    capability_file.write_text(
        """
device_id: dev-structured-glass
user_id: user-structured
device_name: structured-glass
device_role: front_glass
tags: [primary, debug]
supports:
  sensors:
    - type: rgb
      modes: [single, continuous]
      default:
        fps: 2
        sample_count: 1
        duration_seconds: 0
        width: 1280
        height: 720
        format: jpeg
      external:
        facing: environment
    - type: imu
      default:
        sample_rate_hz: 50
        duration_seconds: 5
  actuators:
    - type: vibrator
      default:
        duration_seconds: 0.3
""",
        encoding="utf-8",
    )

    result = compile_device_capabilities_file(capability_file)

    assert set(result["payload"]["supports"]) == {"sensors", "actuators"}
    support_ids = set(result["payload"]["properties"]["realtime_agent.support_ids"])
    assert support_ids == {"sensor.rgb", "sensor.imu", "actuator.haptic"}
    defaults = result["payload"]["properties"]["realtime_agent.support_defaults"]
    assert defaults["sensor.rgb"]["frequency_hz"] == 2
    assert defaults["sensor.rgb"]["formats"] == ["jpeg"]
    assert defaults["sensor.imu"]["frequency_hz"] == 50
    assert defaults["actuator.haptic"]["duration_seconds"] == 0.3
    assert result["payload"]["properties"]["device_role"] == "front_glass"
    assert result["payload"]["properties"]["tags"] == ["primary", "debug"]
    assert {"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}} in compile_internal_routes_from_supports(result["payload"]["supports"])
    assert {"event": "command.*", "filter": {"payload.command": "haptic.vibrate"}} in compile_internal_routes_from_supports(result["payload"]["supports"])


def test_registration_accepts_supports_and_routes_compiled_events(tmp_path: Path) -> None:
    """测试目标：验证注册请求只带 supports 时，server 能编译订阅并完成事件路由。

    测试方法：注册一台支持 RGB 的设备，不传 routes；随后发布 RGB 控制事件。
    预期结果：设备收到事件，debug 快照中能看到编译后的订阅和 support id。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    connection = FakeConnection("dev-browser")
    payload = compile_registration_payload(
        {
            "device_id": "dev-browser",
            "name": "browser",
            "supports": {"sensors": [{"type": "rgb", "modes": ["single"], "default": {"format": "jpeg"}}]},
        }
    )
    app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id="user-001",
            producer_id="dev-browser",
            payload=payload,
        ),
        connection,
    )

    app.publish_control_event(
        Event(
            event_name="stream.control.open.requested",
            user_id="user-001",
            producer_id="server-main",
            stream_type="sensor.rgb",
            payload={"mode": "single", "format": "jpeg"},
        )
    )

    snapshot = app.control_service.build_device_snapshot("dev-browser")
    assert len(connection.events) == 1
    assert connection.events[-1].stream_type == "sensor.rgb"
    assert snapshot["properties"]["realtime_agent.support_ids"] == ["sensor.rgb"]
    assert {"event": "control.audio_session.*"} in compile_internal_routes_from_supports(payload["supports"])
    assert {"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}} in compile_internal_routes_from_supports(payload["supports"])


def test_registration_compiles_audio_session_route_for_browser_wake_flow(tmp_path: Path) -> None:
    """测试目标：验证结构化 supports 注册后，wake 能把 open.requested 路由回设备。

    测试方法：注册一台只声明 RGB 的浏览器设备，然后发布 `control.user.wake.detected`。
    预期结果：设备仍能收到 `control.audio_session.open.requested`，避免浏览器端拿不到 sessionId。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    connection = FakeConnection("dev-browser")
    payload = compile_registration_payload(
        {
            "device_id": "dev-browser",
            "name": "browser",
            "supports": {"sensors": [{"type": "rgb", "modes": ["single"], "default": {"format": "jpeg"}}]},
        }
    )
    app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id="user-001",
            producer_id="dev-browser",
            payload=payload,
        ),
        connection,
    )

    app.publish_control_event(
        Event(
            event_name="control.user.wake.detected",
            user_id="user-001",
            producer_id="dev-browser",
            payload={"wake_source": "browser_device_button"},
        )
    )

    assert any(event.event_name == "control.audio_session.open.requested" for event in connection.events)


def test_visual_display_properties_compile_to_rgb_input_route() -> None:
    """测试目标：验证视觉显示设备可通过 properties 订阅 RGB 输入流。

    测试方法：分别使用 `actuator.display.rgb` 和 `endpoint.role.visual_display`
    声明显示能力，检查系统路由编译结果。
    预期结果：编译得到 `stream.input.* + sensor.rgb`，且不会生成上游相机控制路由。
    """

    routes = compile_system_routes_from_properties(
        {
            "actuator.display.rgb": True,
            "endpoint.role.visual_display": "true",
        }
    )

    assert {"event": "stream.input.*", "filter": {"stream_type": "sensor.rgb"}} in routes
    assert {"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}} not in routes


def test_visual_display_property_disabled_does_not_subscribe_rgb_input() -> None:
    """测试目标：验证未声明显示能力的普通设备不会消费 RGB 输入流。

    测试方法：传入空 properties 和显式 false 字符串。
    预期结果：不会生成 `sensor.rgb` 输入流消费者路由，避免所有设备都收到视频帧。
    """

    assert compile_system_routes_from_properties({}) == []
    assert compile_system_routes_from_properties({"actuator.display.rgb": "false"}) == []


def test_peer_video_properties_compile_to_command_route() -> None:
    """测试目标：验证 peer video 端点能通过 properties 订阅远程命令。

    测试方法：分别声明 phone receiver 和 glass sender 的 peer video 属性。
    预期结果：server 注册阶段生成 `command.*` 路由，TaskContext.commands 能找到设备。
    """

    receiver_routes = compile_system_routes_from_properties({"peer.video.receiver": True})
    sender_routes = compile_system_routes_from_properties({"peer.video.sender": "true"})

    assert {"event": "command.*"} in receiver_routes
    assert {"event": "command.*"} in sender_routes


def test_sensor_tof_stream_is_stored_as_asset(tmp_path: Path) -> None:
    """测试目标：验证 ToF 相机不是只停留在注册语义中，也能作为资产流入库。

    测试方法：打开 `sensor.tof` 输入流并写入一个 final chunk。
    预期结果：Asset Service 能按 `sensor.tof` 查询到最新资产。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    handle = app.open_input_stream(user_id="user-001", producer_id="dev-tof", stream_type="sensor.tof")
    app.write_input_chunk(
        StreamChunk(
            user_id="user-001",
            session_id=handle.session_id,
            stream_id=handle.stream_id,
            stream_type="sensor.tof",
            seq=0,
            payload=b"tof-depth-frame",
            final=True,
        )
    )

    asset = app.asset_service.store.latest(user_id="user-001", stream_type="sensor.tof")
    assert asset is not None
    assert asset.metadata["payload_size"] == len(b"tof-depth-frame")


def test_device_validate_cli_outputs_compiled_json(capsys) -> None:
    """测试目标：验证设备能力校验命令可供开发者本地检查。

    测试方法：直接调用 CLI 函数并要求输出 JSON。
    预期结果：输出包含注册 payload 和编译后的 routes。
    """

    from realtime_agent.cli.device import validate

    validate(["examples/dev-support/devices/browser-glass/device.realtime-agent.yaml", "--json"])

    output = json.loads(capsys.readouterr().out)
    assert output["payload"]["device_id"] == "dev-browser-glass-001"
    assert compile_internal_routes_from_supports(output["payload"]["supports"])
