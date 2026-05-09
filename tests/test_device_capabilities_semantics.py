from __future__ import annotations

import json
from pathlib import Path

import pytest

from audio_chat import AudioChatApp, AudioChatConfig, Event, StreamChunk
from audio_chat.device_capabilities import compile_device_capabilities_file, compile_registration_payload, compile_supports_to_subscriptions


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


def test_browser_device_capability_file_compiles_to_subscriptions() -> None:
    """测试目标：验证浏览器设备能力文件能编译成协议订阅。

    测试方法：读取 `device.audio-chat.yaml`，检查结构化能力和编译产物。
    预期结果：设备开发者不需要手写 subscriptions，也能得到 RGB、IMU、ToF 和 haptic 订阅。
    """

    result = compile_device_capabilities_file("device-examples/browser-glass/device.audio-chat.yaml")

    support_ids = {item["id"] for item in result["payload"]["supports"]}
    assert support_ids == {
        "sensor.rgb",
        "sensor.imu",
        "sensor.tof",
        "actuator.haptic",
    }
    subscriptions = result["payload"]["subscriptions"]
    assert {"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}} in subscriptions
    assert {"event": "stream.control.*", "filter": {"stream_type": "sensor.imu"}} in subscriptions
    assert {"event": "stream.control.*", "filter": {"stream_type": "sensor.tof"}} in subscriptions
    assert {"event": "stream.output.*", "filter": {"stream_type": "actuator.haptic"}} in subscriptions
    assert {"event": "command.*", "filter": {"payload.command": "haptic.vibrate"}} in subscriptions


def test_unknown_support_id_fails_fast() -> None:
    """测试目标：验证语义 ID 写错时能被校验拦住。

    测试方法：构造 `sensor.rbg` 拼写错误。
    预期结果：编译阶段抛出 ValueError，避免端侧带着错误订阅启动。
    """

    with pytest.raises(ValueError, match="unknown support id"):
        compile_supports_to_subscriptions({"sensors": [{"type": "rbg"}]})


def test_structured_supports_compile_to_legacy_subscriptions(tmp_path: Path) -> None:
    """测试目标：验证新版结构化能力声明能被 server 编译成当前路由订阅。

    测试方法：构造 `supports.sensors[].type` 和 `supports.actuators[].type` 写法，
    读取编译结果中的标准 support id、默认参数映射和订阅。
    预期结果：设备开发者可以按传感器/执行器语义写配置，server 内部仍得到稳定的
    `sensor.*` / `actuator.*` 能力和订阅。
    """

    capability_file = tmp_path / "device.audio-chat.yaml"
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

    supports = {item["id"]: item for item in result["payload"]["supports"]}
    assert set(supports) == {"sensor.rgb", "sensor.imu", "actuator.haptic"}
    assert supports["sensor.rgb"]["frequency_hz"] == 2
    assert supports["sensor.rgb"]["formats"] == ["jpeg"]
    assert supports["sensor.imu"]["frequency_hz"] == 50
    assert supports["actuator.haptic"]["commands"] == ["vibrate"]
    assert result["payload"]["properties"]["device_role"] == "front_glass"
    assert result["payload"]["properties"]["tags"] == ["primary", "debug"]
    assert {"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}} in result["payload"]["subscriptions"]
    assert {"event": "command.*", "filter": {"payload.command": "haptic.vibrate"}} in result["payload"]["subscriptions"]


def test_registration_accepts_supports_and_routes_compiled_events(tmp_path: Path) -> None:
    """测试目标：验证注册请求只带 supports 时，server 能编译订阅并完成事件路由。

    测试方法：注册一台支持 RGB 的设备，不传 subscriptions；随后发布 RGB 控制事件。
    预期结果：设备收到事件，debug 快照中能看到编译后的订阅和 support id。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
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
    assert snapshot["properties"]["audio_chat.support_ids"] == ["sensor.rgb"]
    assert snapshot["subscriptions"] == [{"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}}]


def test_sensor_tof_stream_is_stored_as_asset(tmp_path: Path) -> None:
    """测试目标：验证 ToF 相机不是只停留在注册语义中，也能作为资产流入库。

    测试方法：打开 `sensor.tof` 输入流并写入一个 final chunk。
    预期结果：Asset Service 能按 `sensor.tof` 查询到最新资产。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
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
    预期结果：输出包含注册 payload 和编译后的 subscriptions。
    """

    from audio_chat.cli.device import validate

    validate(["device-examples/browser-glass/device.audio-chat.yaml", "--json"])

    output = json.loads(capsys.readouterr().out)
    assert output["payload"]["device_id"] == "dev-browser-glass-001"
    assert output["payload"]["subscriptions"]
