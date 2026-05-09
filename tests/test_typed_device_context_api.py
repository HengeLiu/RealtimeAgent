from __future__ import annotations

import asyncio

import pytest

from audio_chat import (
    AmbiguousDeviceError,
    AudioChatApp,
    AudioChatConfig,
    CommandResult,
    DeviceNotFoundError,
    ToolContextFactory,
)
from audio_chat.protocol import Event


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
    app: AudioChatApp,
    endpoint: RecordingEndpoint,
    *,
    routes: list[dict],
    properties: dict | None = None,
) -> None:
    response = app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id=endpoint.user_id,
            producer_id=endpoint.device_id,
            payload={
                "device_id": endpoint.device_id,
                "device_name": endpoint.device_id,
                "client_type": "typed-context-test",
                "sdk_version": "audio-chat-test",
                "auth": {"mode": "disabled"},
                "routes": routes,
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

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    context = ToolContextFactory(app=app).create(user_id="user-typed-tool", session_id="sess-typed-tool")

    assert context.devices.sensors.rgb is not None
    assert context.devices.actuators.vibrator is not None
    assert context.devices.commands is not None
    assert context.output is not None
    assert context.assets is not None

    with pytest.raises(Exception) as exc:
        context.devices.sensors.rgb.stream()
    assert "only available in TaskContext" in str(exc.value)


def test_sensor_one_requires_unique_matching_device(tmp_path) -> None:
    """测试目标：确认 typed sensor one 不在多设备匹配时偷偷选择第一台。

    测试方法：注册两台都支持 sensor.rgb 的设备，然后调用 `rgb.one()`。
    预期结果：抛出 AmbiguousDeviceError，要求调用方补充 selector。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), asset_request_timeout_seconds=0.01))
    user_id = "user-typed-ambiguous"
    for device_id in ("dev-rgb-1", "dev-rgb-2"):
        register_endpoint(
            app,
            RecordingEndpoint(user_id=user_id, device_id=device_id),
            routes=[{"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}}],
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

    from audio_chat import StreamTimeoutError

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), asset_request_timeout_seconds=0.01))
    user_id = "user-typed-selector"
    front = RecordingEndpoint(user_id=user_id, device_id="dev-front")
    side = RecordingEndpoint(user_id=user_id, device_id="dev-side")
    register_endpoint(
        app,
        front,
        routes=[{"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}}],
        properties={"device_role": "front_glass"},
    )
    register_endpoint(
        app,
        side,
        routes=[{"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}}],
        properties={"device_role": "side_camera"},
    )

    context = ToolContextFactory(app=app).create(user_id=user_id, session_id="sess-typed-selector")

    async def _run() -> None:
        with pytest.raises(StreamTimeoutError):
            await context.devices.sensors.rgb.one(selector={"device_role": "front_glass"}, timeout_seconds=0.01)

    asyncio.run(_run())
    assert [event.event_name for event in front.events] == ["stream.control.open.requested"]
    assert side.events == []


def test_commands_call_returns_stable_result(tmp_path) -> None:
    """测试目标：确认 Commands API 不暴露底层 PublishResult。

    测试方法：注册一台订阅设备命令的端侧，调用 `commands.call()`。
    预期结果：返回 CommandResult，并且端侧收到控制事件。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    user_id = "user-typed-command"
    endpoint = RecordingEndpoint(user_id=user_id, device_id="dev-command")
    register_endpoint(
        app,
        endpoint,
        routes=[{"event": "command.*"}],
    )
    context = ToolContextFactory(app=app).create(user_id=user_id, session_id="sess-typed-command")

    async def _run() -> CommandResult:
        return await context.devices.commands.call(name="device.camera.set_zoom", params={"zoom": 2.0})

    result = asyncio.run(_run())

    assert isinstance(result, CommandResult)
    assert result.ok is True
    assert result.device_count == 1
    assert endpoint.events[-1].payload["command"] == "device.camera.set_zoom"
    assert endpoint.events[-1].payload["zoom"] == 2.0


def test_commands_call_selector_routes_only_matching_device(tmp_path) -> None:
    """测试目标：确认 Commands API 的 selector 会影响真实投递，不只是前置校验。

    测试方法：注册两台都订阅命令的设备，只让其中一台匹配 `device_role`。
    预期结果：只有匹配设备收到命令事件。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    user_id = "user-typed-command-selector"
    phone = RecordingEndpoint(user_id=user_id, device_id="dev-phone")
    glass = RecordingEndpoint(user_id=user_id, device_id="dev-glass")
    register_endpoint(
        app,
        phone,
        routes=[{"event": "command.*"}],
        properties={"device_role": "phone"},
    )
    register_endpoint(
        app,
        glass,
        routes=[{"event": "command.*"}],
        properties={"device_role": "front_glass"},
    )
    context = ToolContextFactory(app=app).create(user_id=user_id, session_id="sess-typed-command-selector")

    async def _run() -> CommandResult:
        return await context.devices.commands.call(
            name="device.camera.set_zoom",
            selector={"device_role": "front_glass"},
            params={"zoom": 1.5},
        )

    result = asyncio.run(_run())

    assert result.ok is True
    assert phone.events == []
    assert [event.payload["command"] for event in glass.events] == ["device.camera.set_zoom"]
