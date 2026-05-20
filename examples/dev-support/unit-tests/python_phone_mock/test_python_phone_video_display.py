from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from aiohttp import ClientSession, web

from realtime_agent.app import RealtimeAgentApp, RealtimeAgentConfig
from realtime_agent.protocol import Event, StreamChunk, StreamChunkCodec
from realtime_agent.server import RealtimeAgentHttpServer


REPO_ROOT = Path(__file__).resolve().parents[4]
PYTHON_PHONE_ROOT = REPO_ROOT / "examples" / "dev-support" / "devices" / "python-phone"
PYTHON_GLASS_ROOT = REPO_ROOT / "examples" / "dev-support" / "devices" / "python-glass"
for path in (PYTHON_PHONE_ROOT, PYTHON_GLASS_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from realtime_agent_python_phone_mock.phone_mock import NetworkPythonPhoneMockEndpoint  # noqa: E402
from realtime_agent_python_phone_mock.phone_mock import OpenCvVideoPreview  # noqa: E402
from realtime_agent_python_phone_mock.gui import GuiEventBridge  # noqa: E402


class FakeCv2:
    """用于验证 OpenCV 预览窗口调用顺序的轻量假对象。"""

    WINDOW_NORMAL = 0
    FONT_HERSHEY_SIMPLEX = 0
    LINE_AA = 0

    def __init__(self) -> None:
        self.calls: list[str] = []

    def namedWindow(self, *_args) -> None:  # noqa: N802 - 模拟 OpenCV API
        self.calls.append("namedWindow")

    def putText(self, *_args) -> None:  # noqa: N802 - 模拟 OpenCV API
        self.calls.append("putText")

    def imshow(self, *_args) -> None:
        self.calls.append("imshow")

    def waitKey(self, *_args) -> int:  # noqa: N802 - 模拟 OpenCV API
        self.calls.append("waitKey")
        return 0

    def destroyWindow(self, *_args) -> None:  # noqa: N802 - 模拟 OpenCV API
        self.calls.append("destroyWindow")


def test_python_phone_preview_shows_placeholder_on_startup() -> None:
    """测试目标：确认 Python 手机端启动后立即弹出可见窗口占位画面。

    测试方法：使用假的 cv2 模块实例化 `OpenCvVideoPreview`，观察调用顺序。
    预期结果：初始化时调用 `namedWindow`、`imshow` 和 `waitKey`，即使还没有收到视频帧。
    """

    fake_cv2 = FakeCv2()

    OpenCvVideoPreview(cv2_module=fake_cv2, enabled=True)

    assert fake_cv2.calls[:4] == ["namedWindow", "putText", "imshow", "waitKey"]


def test_python_phone_gui_event_bridge_records_status_log_and_frame() -> None:
    """测试目标：验证 Python 手机 GUI 的无界面事件桥可在 CI 中独立测试。

    测试方法：发布状态、日志和一帧假图像，读取 bridge 快照。
    预期结果：事件桥记录关键状态和最近帧信息，不依赖 PySide6 窗口。
    """

    class FakeImage:
        shape = (12, 34, 3)

    class FakeFrame:
        stream_id = "stream-rgb-001"
        stream_type = "sensor.rgb"
        seq = 7
        codec = "jpeg"
        image = FakeImage()

        @property
        def width(self) -> int:
            return 34

        @property
        def height(self) -> int:
            return 12

    bridge = GuiEventBridge(log_limit=2, show_debug_events=True)

    bridge.emit_status(control="open", stream="open", registered=True)
    bridge.emit_log("INFO", "registered")
    summary = bridge.emit_frame(FakeFrame())

    snapshot = bridge.snapshot()
    assert summary.width == 34
    assert snapshot["status"]["registered"] is True
    assert snapshot["status"]["frame_count"] == 1
    assert snapshot["latest_frame"]["stream_id"] == "stream-rgb-001"
    assert snapshot["logs"][-1]["message"] == "registered"


def test_sensor_rgb_input_stream_routes_to_python_phone_video_display(tmp_path: Path) -> None:
    """测试目标：验证眼镜端 `sensor.rgb` 视频 stream 能通过 server 回显到 Python 手机端。

    测试方法：启动真实 aiohttp server，注册 Python 手机端并订阅 `stream.input.*`
    的 `sensor.rgb`；再注册一台眼镜端并上传 PNG 帧。
    预期结果：Python 手机端收到并解码视频帧，保存最近帧，server 记录的消费者包含手机端。
    """

    async def run() -> None:
        audio_app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
        server = RealtimeAgentHttpServer(audio_app)
        runner = web.AppRunner(server.create_web_app())
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001
        latest_frame = tmp_path / "latest-rgb.png"
        phone = NetworkPythonPhoneMockEndpoint(
            server_url=f"http://127.0.0.1:{port}",
            user_id="user-video",
            device_id="dev-python-phone-display",
            runs_root=str(tmp_path / "runs"),
            properties={
                "endpoint.role.visual_display": True,
                "endpoint.compute.vision": True,
                "actuator.display.rgb": True,
            },
            supports={"sensors": [], "actuators": []},
            display={"enabled": False, "save_latest_frame": str(latest_frame)},
        )
        tasks: list[asyncio.Task] = []
        try:
            async with ClientSession() as session:
                phone_control = await phone.run_until_registered(session=session)
                phone_stream = await session.ws_connect(phone._stream_url())  # noqa: SLF001
                tasks.extend(
                    [
                        asyncio.create_task(phone._control_loop(phone_control, phone_stream, None)),  # noqa: SLF001
                        asyncio.create_task(phone._stream_loop(phone_control, phone_stream)),  # noqa: SLF001
                    ]
                )

                glass_device_id = "dev-python-glass-camera"
                glass_control = await session.ws_connect(_control_url(port))
                await _send_event(
                    glass_control,
                    Event(
                        event_name="control.device.register.requested",
                        user_id="user-video",
                        producer_id=glass_device_id,
                        payload={
                            "device_id": glass_device_id,
                            "name": "Python 眼镜视频源",
                            "client_type": "python-glass",
                            "auth": {"mode": "disabled"},
                            "properties": {"sensor.rgb": True},
                            "supports": {"sensors": [], "actuators": []},
                        },
                    ),
                )
                registered = await _receive_event(glass_control)
                assert registered.event_name == "control.device.registered"
                glass_stream = await session.ws_connect(_stream_url(port, glass_device_id))

                stream_id = "stream_rgb_preview"
                await _send_event(
                    glass_control,
                    Event(
                        event_name="stream.input.opened",
                        user_id="user-video",
                        producer_id=glass_device_id,
                        session_id=glass_device_id,
                        stream_id=stream_id,
                        stream_type="sensor.rgb",
                        payload={
                            "stream_type": "sensor.rgb",
                            "format": {"codec": "png", "sample_rate": 1, "channels": 1, "chunk_ms": 1},
                        },
                    ),
                )
                await glass_stream.send_bytes(
                    StreamChunkCodec.encode(
                        StreamChunk(
                            user_id="user-video",
                            session_id=glass_device_id,
                            stream_id=stream_id,
                            stream_type="sensor.rgb",
                            seq=0,
                            payload=_png_1x1(),
                            codec="png",
                            sample_rate=1,
                            channels=1,
                            duration_ms=1,
                            final=True,
                        )
                    )
                )
                await _wait_for(lambda: len(phone.video_frames) == 1)
                await _send_event(
                    glass_control,
                    Event(
                        event_name="stream.input.closed",
                        user_id="user-video",
                        producer_id=glass_device_id,
                        session_id=glass_device_id,
                        stream_id=stream_id,
                        stream_type="sensor.rgb",
                        payload={"stream_type": "sensor.rgb", "reason": "test_done"},
                    ),
                )
                await glass_stream.close()
                await glass_control.close()
                await phone_stream.close()
                await phone_control.close()
        finally:
            for task in tasks:
                task.cancel()
            await runner.cleanup()

        assert phone.video_errors == []
        assert phone.video_frames[0]["stream_id"] == "stream_rgb_preview"
        assert phone.video_frames[0]["width"] == 1
        assert phone.video_frames[0]["height"] == 1
        assert latest_frame.exists()
        handle = audio_app.stream_service.registry.get("stream_rgb_preview")
        assert handle.consumer_device_ids == ("dev-python-phone-display",)

    asyncio.run(run())


def _control_url(port: int) -> str:
    """返回测试 server 的控制 WebSocket 地址。"""

    return f"ws://127.0.0.1:{port}/ws/control"


def _stream_url(port: int, device_id: str) -> str:
    """返回测试 server 的 stream WebSocket 地址。"""

    return f"ws://127.0.0.1:{port}/ws/stream?device_id={device_id}"


async def _send_event(ws, event: Event) -> None:
    """通过 WebSocket 发送协议事件。"""

    await ws.send_str(json.dumps(event.to_dict(), ensure_ascii=False))


async def _receive_event(ws) -> Event:
    """从 WebSocket 读取一条协议事件。"""

    message = await ws.receive(timeout=5)
    return Event.from_dict(json.loads(message.data))


async def _wait_for(predicate) -> None:
    """等待异步条件成立。"""

    deadline = asyncio.get_running_loop().time() + 5
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met before timeout")


def _png_1x1() -> bytes:
    """生成合法的 1x1 PNG 测试帧。"""

    import cv2  # type: ignore
    import numpy as np  # type: ignore

    image = np.zeros((1, 1, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("failed to encode png test frame")
    return bytes(encoded)
