import asyncio
import json

import pytest
from aiohttp import web

from realtime_agent_device import RealtimeAgentDeviceClient, DeviceBuilder, StreamChunkCodec


pytestmark = [pytest.mark.sdk, pytest.mark.device_sdk]


async def _run_contract() -> None:
    received_events: list[dict] = []
    received_chunks: list[dict] = []

    async def control_ws(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for message in ws:
            item = json.loads(message.data)
            received_events.append(item)
            if item["event_name"] == "control.device.register.requested":
                await ws.send_str(
                    json.dumps(
                        {
                            "version": "realtime-agent.v1",
                            "event_id": "evt_registered",
                            "event_name": "control.device.registered",
                            "timestamp_ms": 1,
                            "user_id": item["user_id"],
                            "producer_id": "server-main",
                            "payload": {
                                "device_id": item["payload"]["device_id"],
                                "connection_id": "conn-test",
                                "heartbeat_interval_seconds": 60
                            }
                        }
                    )
                )
                await ws.send_str(
                    json.dumps(
                        {
                            "version": "realtime-agent.v1",
                            "event_id": "evt_stream_open",
                            "event_name": "stream.control.open.requested",
                            "timestamp_ms": 2,
                            "user_id": item["user_id"],
                            "producer_id": "server-main",
                            "session_id": item["payload"]["device_id"],
                            "stream_id": "stream-rgb-test",
                            "stream_type": "sensor.rgb",
                            "payload": {"request_id": "req-test", "mode": "single"}
                        }
                    )
                )
        return ws

    async def visual_input_ws(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for message in ws:
            received_chunks.append(StreamChunkCodec.decode_header(message.data))
        return ws

    app = web.Application()
    app.router.add_get("/ws/control", control_ws)
    app.router.add_get("/ws/stream/visual/input", visual_input_ws)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    device = DeviceBuilder.define("dev-python-001").user("user-001").sensor_rgb(format="jpeg")
    client = RealtimeAgentDeviceClient(server_url=f"http://127.0.0.1:{port}", device=device)

    async def handle_rgb(request) -> None:
        await request.opened({"request_id": "req-test"})
        await request.write(b"abc", codec="jpeg", sample_rate=1, channels=1, final=True, metadata={"request_id": "req-test"})
        await request.closed()

    client.on_stream_open("sensor.rgb", handle_rgb)
    try:
        await client.connect()
        await client.register()
        event = await client.receive_event(timeout=2)
        assert await client.dispatch_event(event)
        await asyncio.sleep(0.1)
    finally:
        await client.close()
        await runner.cleanup()

    event_names = [item["event_name"] for item in received_events]
    assert "control.device.register.requested" in event_names
    assert "stream.input.opened" in event_names
    assert "stream.input.closed" in event_names
    assert received_chunks[0]["stream_type"] == "sensor.rgb"
    assert received_chunks[0]["payload"] == b"abc"


def test_python_device_client_registers_and_uploads_stream_over_real_websocket() -> None:
    """测试目标：验证 Python Device SDK 能通过真实 WebSocket 完成注册和 RGB 上传。

    测试方法：启动最小 aiohttp server，SDK 连接 `/ws/control` 和 `/ws/stream/visual/input`，
    收到 `stream.control.open.requested` 后上传一帧 JPEG payload。
    预期结果：server 收到注册、stream opened/closed 回执和二进制 chunk。
    """

    asyncio.run(_run_contract())
