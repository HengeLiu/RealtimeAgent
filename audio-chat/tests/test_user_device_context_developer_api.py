from __future__ import annotations

import asyncio
import time

from audio_chat import AudioChatApp, AudioChatConfig, Event, StreamChunk, UserDeviceContext
from audio_chat.endpoints import PythonPlaybackEndpoint


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


def _register_device(app: AudioChatApp, *, endpoint, user_id: str, device_id: str, capabilities: dict, subscriptions: list[dict]) -> None:
    app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id=user_id,
            producer_id=device_id,
            payload={
                "device_id": device_id,
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
        capabilities={"streams.consume": ["actuator.speaker"], "audio.output": True},
        subscriptions=[{"event": "stream.output.*", "filter": {"stream_type": "actuator.speaker"}}],
    )
    context = UserDeviceContext(user_id="user-notify", app=app)

    handle = context.find_device(capability="actuator.speaker")
    context.notify("测试通知", priority="high", ttl_seconds=5)

    assert handle is not None
    assert handle.snapshot.device_id == "dev-speaker"
    assert not hasattr(handle, "publish_event")
    assert any(event.event_name == "stream.output.open.requested" for event in endpoint.events)
    assert endpoint.chunks
