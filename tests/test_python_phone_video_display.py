from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from aiohttp import ClientSession, web

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.protocol import Event, StreamChunk, StreamChunkCodec
from audio_chat.server import AudioChatHttpServer


REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_PHONE_ROOT = REPO_ROOT / "device-examples" / "python-phone"
PYTHON_GLASS_ROOT = REPO_ROOT / "device-examples" / "python-glass"
for path in (PYTHON_PHONE_ROOT, PYTHON_GLASS_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from audio_chat_python_phone_mock.phone_mock import NetworkPythonPhoneMockEndpoint  # noqa: E402


def test_sensor_rgb_input_stream_routes_to_python_phone_video_display(tmp_path: Path) -> None:
    """测试目标：验证眼镜端 `sensor.rgb` 视频 stream 能通过 server 回显到 Python 手机端。

    测试方法：启动真实 aiohttp server，注册 Python 手机端并订阅 `stream.input.*`
    的 `sensor.rgb`；再注册一台眼镜端并上传 PNG 帧。
    预期结果：Python 手机端收到并解码视频帧，保存最近帧，server 记录的消费者包含手机端。
    """

    async def run() -> None:
        audio_app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
        server = AudioChatHttpServer(audio_app)
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
            subscriptions=[
                {"event": "stream.input.*", "filter": {"stream_type": "sensor.rgb"}},
            ],
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
                            "subscriptions": [],
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
