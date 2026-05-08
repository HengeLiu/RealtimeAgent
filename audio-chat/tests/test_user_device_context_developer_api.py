from __future__ import annotations

import asyncio
import time

from audio_chat import AudioChatApp, AudioChatConfig, Event, StreamChunk, UserDeviceContext
from audio_chat_python_glass.playback import PythonPlaybackEndpoint


class PassiveEndpoint:
    """测试用端侧连接，记录收到的事件和 stream chunk。"""

    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.events = []
        self.chunks = []

    def push_event(self, event: Event) -> None:
        self.events.append(event)

    def push_stream_chunk(self, chunk: object) -> None:
        self.chunks.append(chunk)

    def close(self, *, reason: str) -> None:
        self.events.append(
            Event(
                event_name="control.device.state.changed",
                user_id="user-dev-api",
                producer_id="server-main",
                payload={"reason": reason},
            )
        )


def _register_device(
    app: AudioChatApp,
    *,
    endpoint,
    user_id: str,
    device_id: str,
    capabilities: dict,
    subscriptions: list[dict],
    name: str | None = None,
) -> None:
    app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id=user_id,
            producer_id=device_id,
            payload={
                "device_id": device_id,
                "name": name or device_id,
                "auth": {"mode": "disabled"},
                "capabilities": capabilities,
                "subscriptions": subscriptions,
            },
        ),
        endpoint,
    )


def test_capture_photo_uses_sensor_rgb_asset_stream(tmp_path) -> None:
    """测试目标：验证 `capture_photo()` 是旧抓拍写法的安全迁移入口。

    测试方法：注册可生产 `sensor.rgb` 的 playback 端侧，再调用 `capture_photo()`。
    预期结果：服务端发布 stream 配置事件，图片字节通过 `sensor.rgb` stream 进入资产缓存。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    endpoint = PythonPlaybackEndpoint(app=app, user_id="user-photo", device_id="dev-camera")
    _register_device(
        app,
        endpoint=endpoint,
        user_id="user-photo",
        device_id="dev-camera",
        capabilities={"streams.produce": ["sensor.rgb"], "sensor.rgb": True},
        subscriptions=[{"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}}],
    )

    asset = UserDeviceContext(user_id="user-photo", app=app).capture_photo(reason="parity-test", timeout_seconds=1)

    assert asset is not None
    assert asset.stream_type == "sensor.rgb"
    assert asset.mime_type == "image/jpeg"
    request_events = [event for event in endpoint.events if event.event_name == "stream.control.configure.requested"]
    assert request_events
    assert request_events[-1].payload["reason"] == "parity-test"
    assert "request_id" in asset.metadata
    asset_events = (tmp_path / "runs" / "sessions" / app.active_session_id("user-photo") / "assets.jsonl").read_text(
        encoding="utf-8"
    )
    assert "asset.requested" in asset_events
    assert "asset.stored" in asset_events
    assert '"delivered_count": 1' in asset_events


def test_capture_photo_timeout_is_visible_in_session_asset_events(tmp_path) -> None:
    """测试目标：验证抓拍超时时能在当前会话产物中看到明确诊断。

    测试方法：不注册任何 `sensor.rgb` 设备，直接调用 `capture_photo()` 并等待短超时。
    预期结果：返回 None，`assets.jsonl` 包含 request_id、delivered_count=0 和超时事件。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    session_id = app.active_session_id("user-photo-timeout")

    asset = UserDeviceContext(user_id="user-photo-timeout", app=app).capture_photo(
        reason="timeout-test",
        timeout_seconds=0.01,
    )

    assert asset is None
    asset_events = (tmp_path / "runs" / "sessions" / session_id / "assets.jsonl").read_text(encoding="utf-8")
    assert "asset.requested" in asset_events
    assert "asset.request.timeout" in asset_events
    assert '"delivered_count": 0' in asset_events


def test_old_sdk_builtin_tool_names_are_registered_by_default(tmp_path) -> None:
    """测试目标：确认老 SDK 内置工具名在新版 SDK 中默认可见。

    测试方法：创建默认 AudioChatApp，读取 ToolRegistry 名称。
    预期结果：`capture_photo`、`start_phone_video_link` 和
    `close_continuous_dialog` 都无需业务包自动发现即可注册。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))

    assert {
        "capture_photo",
        "start_phone_video_link",
        "close_continuous_dialog",
    } <= set(app.tool_registry.list_names())


def test_builtin_capture_photo_tool_uses_sensor_rgb_asset_stream(tmp_path) -> None:
    """测试目标：验证内置 `capture_photo` Tool 兼容老工具名。

    测试方法：注册可生产 `sensor.rgb` 的 playback 端侧，通过 ToolGateway 调用
    `capture_photo`。
    预期结果：Tool 返回资产引用，端侧收到 stream 配置事件。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    endpoint = PythonPlaybackEndpoint(app=app, user_id="user-photo-tool", device_id="dev-camera-tool")
    _register_device(
        app,
        endpoint=endpoint,
        user_id="user-photo-tool",
        device_id="dev-camera-tool",
        capabilities={"streams.produce": ["sensor.rgb"], "sensor.rgb": True},
        subscriptions=[{"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}}],
    )
    session_id = app.active_session_id("user-photo-tool")

    result = asyncio.run(
        app.tool_gateway.call(
            name="capture_photo",
            user_id="user-photo-tool",
            session_id=session_id,
            input_data={"reason": "builtin-parity", "timeout_seconds": 1},
        )
    )

    assert result.ok is True
    assert result.assets
    assert result.data["stream_type"] == "sensor.rgb"
    assert any(event.event_name == "stream.control.configure.requested" for event in endpoint.events)


def test_start_phone_video_link_tool_publishes_continuous_rgb_config(tmp_path) -> None:
    """测试目标：验证 `start_phone_video_link` 不引入点对点 RPC。

    测试方法：注册订阅 sensor.rgb 配置事件的端侧，通过 ToolGateway 调用工具。
    预期结果：端侧收到 `stream.control.configure.requested`，payload 只包含 stream
    配置和 link_id，不包含 target device 字段。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    endpoint = PassiveEndpoint("dev-video")
    _register_device(
        app,
        endpoint=endpoint,
        user_id="user-video-link",
        device_id="dev-video",
        capabilities={"streams.produce": ["sensor.rgb"], "sensor.rgb": True},
        subscriptions=[{"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}}],
    )
    session_id = app.active_session_id("user-video-link")

    result = asyncio.run(
        app.tool_gateway.call(
            name="start_phone_video_link",
            user_id="user-video-link",
            session_id=session_id,
            input_data={"frame_interval_ms": 250},
        )
    )

    assert result.ok is True
    assert result.data["state"] == "running"
    event = endpoint.events[-1]
    assert event.event_name == "stream.control.configure.requested"
    assert event.stream_type == "sensor.rgb"
    assert event.payload["mode"] == "continuous"
    assert event.payload["frame_interval_ms"] == 250
    assert "target_device_id" not in event.payload
    assert "phone_device_id" not in event.payload


def test_close_continuous_dialog_tool_requests_close_after_reply(tmp_path) -> None:
    """测试目标：验证 `close_continuous_dialog` 复用新版音频会话生命周期。

    测试方法：注册订阅 audio_session 事件的端侧，先打开用户会话，再调用 Tool。
    预期结果：端侧收到 `control.audio_session.close.requested`，close_mode 为
    `close_after_reply`。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    endpoint = PassiveEndpoint("dev-dialog")
    _register_device(
        app,
        endpoint=endpoint,
        user_id="user-dialog",
        device_id="dev-dialog",
        capabilities={"streams.consume": ["actuator.speaker"]},
        subscriptions=[{"event": "control.audio_session.*"}],
    )
    session_id = app.active_session_id("user-dialog")

    result = asyncio.run(
        app.tool_gateway.call(
            name="close_continuous_dialog",
            user_id="user-dialog",
            session_id=session_id,
            input_data={"mode": "after_reply"},
        )
    )

    assert result.ok is True
    assert result.data["close_mode"] == "close_after_reply"
    assert any(
        event.event_name == "control.audio_session.close.requested"
        and event.payload.get("close_mode") == "close_after_reply"
        for event in endpoint.events
    )


def test_latest_asset_and_request_asset_share_asset_cache(tmp_path) -> None:
    """测试目标：验证最新资产读取和显式资产请求都经由 Asset Service。

    测试方法：先写入一帧 `sensor.rgb`，再分别调用 `latest_asset()` 与
    `request_asset(freshness_seconds=...)`。
    预期结果：两者返回同一个缓存资产，不额外构造控制 RPC。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    handle = app.open_input_stream(user_id="user-cache", producer_id="dev-camera", stream_type="sensor.rgb")
    app.write_input_chunk(
        StreamChunk(
            user_id="user-cache",
            session_id=handle.session_id,
            stream_id=handle.stream_id,
            stream_type="sensor.rgb",
            seq=1,
            payload=b"\xff\xd8cached\xff\xd9",
            final=True,
        )
    )
    context = UserDeviceContext(user_id="user-cache", app=app)

    latest = context.latest_asset("sensor.rgb")
    requested = context.request_asset("sensor.rgb", freshness_seconds=30, timeout_seconds=0.01)

    assert latest is not None
    assert requested is not None
    assert latest.asset_id == requested.asset_id


def test_configure_stream_publishes_protocol_event_without_device_id(tmp_path) -> None:
    """测试目标：验证持续 sensor stream 只能通过协议事件配置。

    测试方法：注册订阅 `stream.control.*` 的设备，调用 `configure_stream()`。
    预期结果：设备收到 `stream.control.configure.requested`；事件 payload 不包含点对点设备字段。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    endpoint = PassiveEndpoint("dev-rgb")
    _register_device(
        app,
        endpoint=endpoint,
        user_id="user-stream",
        device_id="dev-rgb",
        capabilities={"streams.produce": ["sensor.rgb"], "sensor.rgb": True},
        subscriptions=[{"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}}],
    )

    result = UserDeviceContext(user_id="user-stream", app=app).configure_stream(
        "sensor.rgb",
        mode="continuous",
        rate_hz=2,
        duration_seconds=3,
    )

    assert result.delivered_count == 1
    event = endpoint.events[-1]
    assert event.event_name == "stream.control.configure.requested"
    assert event.payload["mode"] == "continuous"
    assert event.payload["rate_hz"] == 2
    assert "target_device_id" not in event.payload


def test_watch_assets_filters_by_since_timestamp(tmp_path) -> None:
    """测试目标：验证 Task 可从某个时间点之后持续读取资产。

    测试方法：写入旧帧，记录时间戳，再写入新帧并调用 `watch_assets(since=...)`。
    预期结果：迭代器只返回 since 之后的新资产。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))

    def write(seq: int) -> None:
        handle = app.open_input_stream(user_id="user-watch-api", producer_id="dev-camera", stream_type="sensor.rgb")
        app.write_input_chunk(
            StreamChunk(
                user_id="user-watch-api",
                session_id=handle.session_id,
                stream_id=handle.stream_id,
                stream_type="sensor.rgb",
                seq=seq,
                payload=f"frame-{seq}".encode(),
                final=True,
            )
        )

    write(1)
    since = time.time()
    time.sleep(0.01)
    write(2)

    async def collect() -> list[int]:
        refs = []
        async for ref in UserDeviceContext(user_id="user-watch-api", app=app).watch_assets(
            "sensor.rgb",
            since=since,
            timeout_seconds=0.03,
        ):
            refs.append(ref)
        return [int(ref.metadata["seq"]) for ref in refs]

    assert asyncio.run(collect()) == [2]


def test_notify_enters_output_service_and_find_device_is_read_only(tmp_path) -> None:
    """测试目标：验证通知输出和设备查找都走开发者安全 API。

    测试方法：注册 speaker 设备，调用 `notify()` 和 `find_device()`。
    预期结果：通知进入 output stream；设备句柄只有只读 snapshot，不提供发送事件方法。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    endpoint = PassiveEndpoint("dev-speaker")
    _register_device(
        app,
        endpoint=endpoint,
        user_id="user-notify",
        device_id="dev-speaker",
        name="客厅扬声器模拟设备",
        capabilities={"streams.consume": ["actuator.speaker"], "audio.output": True},
        subscriptions=[{"event": "stream.output.*", "filter": {"stream_type": "actuator.speaker"}}],
    )
    context = UserDeviceContext(user_id="user-notify", app=app)

    handle = context.find_device(capability="actuator.speaker")
    context.notify("测试通知", priority="high", ttl_seconds=5)

    assert handle is not None
    assert handle.snapshot.device_id == "dev-speaker"
    assert handle.snapshot.name == "客厅扬声器模拟设备"
    assert not hasattr(handle, "publish_event")
    assert any(event.event_name == "stream.output.open.requested" for event in endpoint.events)
    assert endpoint.chunks


def test_stream_output_routes_from_subscription_without_find_device_capability(tmp_path) -> None:
    """测试目标：验证 stream 输出路由不依赖 `find_device(capability=...)`。

    测试方法：注册只订阅 `actuator.speaker` 输出事件的设备，不提供 capabilities，
    直接通过 `submit_audio()` 输出。
    预期结果：设备收到 output stream 事件和音频 chunk。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    endpoint = PassiveEndpoint("dev-speaker-subscription-only")
    _register_device(
        app,
        endpoint=endpoint,
        user_id="user-subscription-only",
        device_id="dev-speaker-subscription-only",
        name="仅订阅扬声器设备",
        capabilities={},
        subscriptions=[{"event": "stream.output.*", "filter": {"stream_type": "actuator.speaker"}}],
    )

    context = UserDeviceContext(user_id="user-subscription-only", app=app)
    handle = context.find_device("actuator.speaker")
    context.submit_audio(b"\x00\x00\x01\x00", codec="pcm16le")

    assert handle is None
    assert any(event.event_name == "stream.output.open.requested" for event in endpoint.events)
    assert endpoint.chunks
