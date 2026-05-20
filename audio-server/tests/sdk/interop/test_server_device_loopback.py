from __future__ import annotations

import asyncio
import json
from contextlib import suppress

import pytest
from aiohttp import web

from realtime_agent import RealtimeAgentApp, RealtimeAgentConfig, ToolContextFactory
from realtime_agent.output.service import OutputItem
from realtime_agent.protocol import StreamFormat
from realtime_agent.server import RealtimeAgentHttpServer
from realtime_agent_device import RealtimeAgentDeviceClient, DeviceBuilder


pytestmark = [pytest.mark.sdk, pytest.mark.device_sdk, pytest.mark.interop]


async def _start_loopback_server(app: RealtimeAgentApp) -> tuple[web.AppRunner, str]:
    """启动真实 RealtimeAgentHttpServer，并返回可连接的本地 URL。"""

    server = RealtimeAgentHttpServer(app)
    web_app = server.create_web_app()
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    sockets = site._server.sockets if site._server is not None else []
    if not sockets:
        raise RuntimeError("loopback server did not expose a socket")
    port = sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


async def _run_loopback_contract(tmp_path) -> None:
    app = RealtimeAgentApp(
        RealtimeAgentConfig(
            runs_root=str(tmp_path / "runs"),
            agent_mode="vision",
            default_actuator_speaker=StreamFormat(codec="pcm16le", sample_rate=16000, channels=1, chunk_ms=40),
            asset_request_timeout_seconds=3,
        )
    )
    runner, server_url = await _start_loopback_server(app)
    user_id = "user-loopback"
    device_id = "dev-loopback-python"
    device = (
        DeviceBuilder.define(device_id)
        .user(user_id)
        .name("Loopback Python Device")
        .sensor_rgb(modes=["single"], format="jpeg", frequency_hz=1)
        .property("realtime_agent.audio_output", "actuator.speaker")
    )
    client = RealtimeAgentDeviceClient(server_url=server_url, device=device)
    context = ToolContextFactory(app=app).create(user_id=user_id, session_id=device_id)
    received_events = []
    output_chunks = []
    stop_pump = asyncio.Event()

    async def handle_rgb(request) -> None:
        request_id = str((request.request.payload or {}).get("request_id") or "")
        await request.opened(
            {
                "request_id": request_id,
                "format": {"codec": "jpeg", "sample_rate": 1, "channels": 1, "chunk_ms": 0},
            }
        )
        await request.write(
            b"rgb-data",
            codec="jpeg",
            sample_rate=1,
            channels=1,
            final=True,
            metadata={"request_id": request_id},
        )
        await request.closed()

    async def handle_ping(command) -> None:
        await command.accepted({"message": "accepted"})
        await command.progress({"message": "halfway", "progress": 0.5})
        await command.completed({"message": "pong"})

    async def pump_control_events() -> None:
        while not stop_pump.is_set():
            try:
                event = await client.receive_event(timeout=0.2)
            except TimeoutError:
                continue
            received_events.append(event)
            if await client.dispatch_event(event):
                continue
            if event.event_name == "stream.output.open.requested":
                output_chunks.append(await client.receive_stream_chunk(timeout=2))
                continue
            if event.event_name in {"stream.output.finish.requested", "stream.output.close.requested"}:
                await client.send_event_name(
                    "stream.output.finished",
                    {"stream_type": event.stream_type or "actuator.speaker", "reason": "loopback_played"},
                    session_id=device_id,
                    stream_id=event.stream_id,
                    stream_type=event.stream_type or "actuator.speaker",
                )

    client.on_stream_open("sensor.rgb", handle_rgb)
    client.on_command("device.ping", handle_ping)
    pump_task: asyncio.Task | None = None
    try:
        await client.connect()
        await client.register(start_heartbeat=False)
        await client.ensure_stream()
        pump_task = asyncio.create_task(pump_control_events())

        asset = await asyncio.to_thread(lambda: asyncio.run(context.devices.sensors.rgb.one(timeout_seconds=3)))
        command_result = await context.devices.commands.call(name="device.ping", timeout_seconds=3)
        app.output_service.submit_output(
            OutputItem(user_id=user_id, session_id=device_id, priority="normal"),
            "互操作输出测试。",
        )

        deadline = asyncio.get_running_loop().time() + 3
        while not output_chunks and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.05)

        assert asset.stream_type == "sensor.rgb"
        assert asset.size_bytes == len(b"rgb-data")
        assert command_result.ok is True
        assert command_result.data["completed_count"] == 1
        assert output_chunks
        assert output_chunks[0].stream_type == "actuator.speaker"
        assert output_chunks[0].payload

        event_names = [event.event_name for event in received_events]
        assert "stream.control.open.requested" in event_names
        assert "command.requested" in event_names
        assert "stream.output.open.requested" in event_names
        assert "stream.output.finish.requested" in event_names

        report = {
            "server_url": server_url,
            "device_id": device_id,
            "received_event_names": event_names,
            "output_chunk_count": len(output_chunks),
            "asset_id": asset.asset_id,
            "command_id": command_result.command_id,
        }
        report_path = tmp_path / "runs" / "loopback-contract-report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finally:
        stop_pump.set()
        if pump_task is not None:
            pump_task.cancel()
            with suppress(asyncio.CancelledError):
                await pump_task
        await client.close()
        await runner.cleanup()


def test_python_device_sdk_interoperates_with_real_server_sdk_websocket(tmp_path) -> None:
    """测试目标：验证真实 Server SDK 与 Python Device SDK 可以通过 WebSocket 闭环。

    测试方法：启动 `RealtimeAgentHttpServer`，用 `RealtimeAgentDeviceClient` 连接真实
    `/ws/control` 和 `/ws/stream`，依次触发 RGB 采集、设备命令和输出 stream。
    预期结果：server 获得 RGB asset 和 command.completed，device 收到 output
    stream chunk，并生成互操作测试报告。
    """

    asyncio.run(_run_loopback_contract(tmp_path))
