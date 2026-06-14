from __future__ import annotations

import asyncio

import pytest

from realtime_agent import (
    AmbiguousDeviceError,
    RealtimeAgentApp,
    RealtimeAgentConfig,
    CommandResult,
    DeviceNotFoundError,
    ToolContextFactory,
)
from realtime_agent.protocol import Event


pytestmark = pytest.mark.sdk


class RecordingEndpoint:
    def __init__(self, *, user_id: str, device_id: str) -> None:
        self.user_id = user_id
        self.device_id = device_id
        self.events: list[Event] = []
        self.chunks = []
        self.closed_reasons: list[str] = []

    def push_event(self, event: Event) -> None:
        self.events.append(event)

    def push_stream_chunk(self, chunk: object) -> None:
        self.chunks.append(chunk)

    def close(self, *, reason: str) -> None:
        self.closed_reasons.append(reason)


def register_endpoint(
    app: RealtimeAgentApp,
    endpoint: RecordingEndpoint,
    *,
    support_routes: list[dict],
    properties: dict | None = None,
) -> None:
    sensors = []
    actuators = []
    for route in support_routes:
        stream_type = (route.get("filter") or {}).get("stream_type")
        if stream_type == "sensor.rgb":
            sensors.append({"type": "rgb"})
        if stream_type == "sensor.imu":
            sensors.append({"type": "imu"})
        if stream_type == "sensor.tof":
            sensors.append({"type": "tof"})
        if stream_type == "actuator.haptic":
            actuators.append({"type": "vibrator"})
    response = app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id=endpoint.user_id,
            producer_id=endpoint.device_id,
            payload={
                "device_id": endpoint.device_id,
                "device_name": endpoint.device_id,
                "client_type": "typed-context-test",
                "sdk_version": "realtime-agent-test",
                "auth": {"mode": "disabled"},
                "supports": {"sensors": sensors, "actuators": actuators},
                "properties": properties or {},
            },
        ),
        endpoint,
    )
    assert response.event_name == "control.device.registered"


def test_tool_context_exposes_typed_facades_and_blocks_streaming(tmp_path) -> None:
    """测试目标：确认 ToolContext 暴露新版 typed facade，但禁止 Tool 打开持续流。

    测试方法：创建 ToolContext，检查 sensors / actuators / commands / output / assets
    入口，并直接调用 `rgb.stream()`。
    预期结果：短生命周期 Tool 能看到 typed facade，长流接口会返回权限错误。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    context = ToolContextFactory(app=app).create(user_id="user-typed-tool", session_id="sess-typed-tool")

    assert context.devices.sensors.rgb is not None
    assert context.devices.actuators.vibrator is not None
    assert context.devices.commands is not None
    assert context.output is not None
    assert context.assets is not None

    with pytest.raises(Exception) as exc:
        context.devices.sensors.rgb.stream()
    assert "only available to background tools" in str(exc.value)


def test_sensor_one_requires_unique_matching_device(tmp_path) -> None:
    """测试目标：确认 typed sensor one 不在多设备匹配时偷偷选择第一台。

    测试方法：注册两台都支持 sensor.rgb 的设备，然后调用 `rgb.one()`。
    预期结果：抛出 AmbiguousDeviceError，要求调用方补充 selector。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), asset_request_timeout_seconds=0.01))
    user_id = "user-typed-ambiguous"
    for device_id in ("dev-rgb-1", "dev-rgb-2"):
        register_endpoint(
            app,
            RecordingEndpoint(user_id=user_id, device_id=device_id),
            support_routes=[{"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}}],
        )

    context = ToolContextFactory(app=app).create(user_id=user_id, session_id="sess-typed-ambiguous")

    async def _run() -> None:
        with pytest.raises(AmbiguousDeviceError):
            await context.devices.sensors.rgb.one(timeout_seconds=0.01)

    asyncio.run(_run())


def test_sensor_one_selector_can_narrow_device(tmp_path) -> None:
    """测试目标：确认 selector 可以把同能力多设备约束到一台。

    测试方法：注册两台 RGB 设备，只有一台带 `device_role=front_glass` 属性。
    预期结果：不会发生多设备歧义；因为测试端不上传资产，最终以 StreamTimeoutError
    表示已进入真实采集等待流程。
    """

    from realtime_agent import StreamTimeoutError

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), asset_request_timeout_seconds=0.01))
    user_id = "user-typed-selector"
    front = RecordingEndpoint(user_id=user_id, device_id="dev-front")
    side = RecordingEndpoint(user_id=user_id, device_id="dev-side")
    register_endpoint(
        app,
        front,
        support_routes=[{"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}}],
        properties={"device_role": "front_glass"},
    )
    register_endpoint(
        app,
        side,
        support_routes=[{"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}}],
        properties={"device_role": "side_camera"},
    )

    context = ToolContextFactory(app=app).create(user_id=user_id, session_id="sess-typed-selector")

    async def _run() -> None:
        with pytest.raises(StreamTimeoutError):
            await context.devices.sensors.rgb.one(selector={"device_role": "front_glass"}, timeout_seconds=0.01)

    asyncio.run(_run())
    assert [event.event_name for event in front.events] == ["stream.control.open.requested"]
    assert side.events == []


def test_sensor_one_returns_when_endpoint_reports_capture_failed(tmp_path) -> None:
    """测试目标：确认端侧主动上报抓拍失败时，资产请求不会继续等到超时。

    测试方法：注册一台 RGB 设备，后台调用 `rgb.one()` 后模拟端侧带 request_id
    发送 `stream.input.closed reason=capture_failed`。
    预期结果：等待方立即以 StreamTimeoutError 返回，并记录 `asset.request.failed`。
    """

    from realtime_agent import StreamTimeoutError

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), asset_request_timeout_seconds=5))
    user_id = "user-rgb-failed"
    endpoint = RecordingEndpoint(user_id=user_id, device_id="dev-rgb-failed")
    register_endpoint(
        app,
        endpoint,
        support_routes=[{"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}}],
    )
    context = ToolContextFactory(app=app).create(user_id=user_id, session_id="dev-rgb-failed")

    async def _run() -> None:
        task = asyncio.create_task(
            asyncio.to_thread(lambda: asyncio.run(context.devices.sensors.rgb.one(timeout_seconds=5)))
        )
        deadline = asyncio.get_running_loop().time() + 1
        while not endpoint.events and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        assert endpoint.events
        request_id = endpoint.events[-1].payload["request_id"]
        app.publish_control_event(
            Event(
                event_name="stream.input.closed",
                user_id=user_id,
                producer_id="dev-rgb-failed",
                session_id="dev-rgb-failed",
                stream_id="stream_rgb_failed",
                stream_type="sensor.rgb",
                payload={
                    "stream_type": "sensor.rgb",
                    "reason": "capture_failed",
                    "error": "camera permission denied",
                    "request_id": request_id,
                },
            )
        )
        with pytest.raises(StreamTimeoutError):
            await asyncio.wait_for(task, timeout=1)

    asyncio.run(_run())
    assets_log = tmp_path / "runs" / user_id / "dev-rgb-failed" / "assets.jsonl"
    assert "asset.request.failed" in assets_log.read_text(encoding="utf-8")
    assert "camera permission denied" in assets_log.read_text(encoding="utf-8")


def test_commands_call_returns_stable_result(tmp_path) -> None:
    """测试目标：确认 Commands API 不暴露底层 PublishResult。

    测试方法：注册一台订阅设备命令的端侧，调用 `commands.call()`。
    预期结果：返回 CommandResult，并且端侧收到控制事件。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    user_id = "user-typed-command"
    endpoint = RecordingEndpoint(user_id=user_id, device_id="dev-command")
    register_endpoint(
        app,
        endpoint,
        support_routes=[{"event": "command.*"}],
    )
    context = ToolContextFactory(app=app).create(user_id=user_id, session_id="sess-typed-command")

    async def _run() -> CommandResult:
        task = asyncio.create_task(context.devices.commands.call(name="device.camera.set_zoom", params={"zoom": 2.0}))
        deadline = asyncio.get_running_loop().time() + 1
        while not endpoint.events and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        assert endpoint.events
        command_id = endpoint.events[-1].payload["command_id"]
        app.publish_control_event(
            Event(
                event_name="command.completed",
                user_id=user_id,
                producer_id="dev-command",
                payload={"command_id": command_id, "message": "zoom updated", "zoom": 2.0},
            )
        )
        return await asyncio.wait_for(task, timeout=1)

    result = asyncio.run(_run())

    assert isinstance(result, CommandResult)
    assert result.ok is True
    assert result.device_count == 1
    assert endpoint.events[-1].payload["command"] == "device.camera.set_zoom"
    assert endpoint.events[-1].payload["params"]["zoom"] == 2.0


def test_commands_call_selector_routes_only_matching_device(tmp_path) -> None:
    """测试目标：确认 Commands API 的 selector 会影响真实投递，不只是前置校验。

    测试方法：注册两台都订阅命令的设备，只让其中一台匹配 `device_role`。
    预期结果：只有匹配设备收到命令事件。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    user_id = "user-typed-command-selector"
    phone = RecordingEndpoint(user_id=user_id, device_id="dev-phone")
    glass = RecordingEndpoint(user_id=user_id, device_id="dev-glass")
    register_endpoint(
        app,
        phone,
        support_routes=[{"event": "command.*"}],
        properties={"device_role": "phone"},
    )
    register_endpoint(
        app,
        glass,
        support_routes=[{"event": "command.*"}],
        properties={"device_role": "front_glass"},
    )
    context = ToolContextFactory(app=app).create(user_id=user_id, session_id="sess-typed-command-selector")

    async def _run() -> CommandResult:
        task = asyncio.create_task(context.devices.commands.call(
            name="device.camera.set_zoom",
            selector={"device_role": "front_glass"},
            params={"zoom": 1.5},
        ))
        deadline = asyncio.get_running_loop().time() + 1
        while not glass.events and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        assert glass.events
        command_id = glass.events[-1].payload["command_id"]
        app.publish_control_event(
            Event(
                event_name="command.completed",
                user_id=user_id,
                producer_id="dev-glass",
                payload={"command_id": command_id, "message": "zoom updated", "zoom": 1.5},
            )
        )
        return await asyncio.wait_for(task, timeout=1)

    result = asyncio.run(_run())

    assert result.ok is True
    assert phone.events == []
    assert [event.payload["command"] for event in glass.events] == ["device.camera.set_zoom"]
