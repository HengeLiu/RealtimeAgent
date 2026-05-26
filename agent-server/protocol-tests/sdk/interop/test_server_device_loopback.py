from __future__ import annotations

import asyncio
import json
from contextlib import suppress

import pytest
from aiohttp import ClientSession, WSMsgType, web

from realtime_agent import RealtimeAgentApp, RealtimeAgentConfig, ToolContextFactory
from realtime_agent.output.service import OutputItem
from realtime_agent.protocol import Event, StreamChunk, StreamChunkCodec, StreamFormat
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


async def _receive_event(ws, *, timeout: float = 2.0) -> Event:
    """从 WebSocket 读取一个 JSON 控制事件。

    测试目标：让拆分 WebSocket 测试直接验证真实 server 返回的协议事件。
    测试方法：读取一帧 TEXT 消息并按 `Event` 解码。
    预期结果：收到合法 JSON 控制事件；超时或类型错误时断言失败。
    """

    message = await ws.receive(timeout=timeout)
    assert message.type == WSMsgType.TEXT
    return Event.from_dict(json.loads(message.data))


async def _register_raw_device(session: ClientSession, server_url: str, *, user_id: str, device_id: str):
    """通过裸 aiohttp WebSocket 注册一个测试设备。

    测试目标：避免复用旧 Python Device SDK 的 `/ws/stream` 兼容入口，直接覆盖新链路。
    测试方法：连接 `/ws/control`，发送注册事件，等待 `control.device.registered`。
    预期结果：server 接受注册，并允许后续按 device_id 建立三条媒体 WebSocket。
    """

    ws_base = server_url.replace("http://", "ws://").replace("https://", "wss://")
    control = await session.ws_connect(f"{ws_base}/ws/control")
    await control.send_str(
        json.dumps(
            Event(
                event_name="control.device.register.requested",
                user_id=user_id,
                producer_id=device_id,
                payload={
                    "device_id": device_id,
                    "name": "Raw Split Stream Device",
                    "client_type": "raw-test-device",
                    "auth": {"mode": "disabled"},
                    "properties": {
                        "realtime_agent.audio_input": "sensor.mic",
                        "realtime_agent.audio_output": "actuator.speaker",
                    },
                    "supports": {"sensors": [{"type": "rgb", "modes": ["single"], "default": {"format": "jpeg", "sample_count": 1}}]},
                },
            ).to_dict(),
            ensure_ascii=False,
        )
    )
    registered = await _receive_event(control)
    assert registered.event_name == "control.device.registered"
    return control


def _chunk(
    *,
    user_id: str,
    device_id: str,
    stream_id: str,
    stream_type: str,
    payload: bytes,
    codec: str,
    seq: int = 0,
    sample_rate: int = 16000,
    channels: int = 1,
    duration_ms: int = 20,
    metadata: dict | None = None,
) -> bytes:
    """构造一帧协议二进制 chunk。

    测试目标：统一生成 mic、rgb 和 speaker chunk，避免测试手写二进制。
    测试方法：使用生产 `StreamChunkCodec.encode`。
    预期结果：返回可直接写入媒体 WebSocket 的 bytes。
    """

    return StreamChunkCodec.encode(
        StreamChunk(
            user_id=user_id,
            session_id=device_id,
            stream_id=stream_id,
            stream_type=stream_type,
            seq=seq,
            payload=payload,
            final=True,
            codec=codec,
            sample_rate=sample_rate,
            channels=channels,
            duration_ms=duration_ms,
            metadata=dict(metadata or {}),
        )
    )


async def _run_split_stream_contract(tmp_path) -> None:
    app = RealtimeAgentApp(
        RealtimeAgentConfig(
            runs_root=str(tmp_path / "runs"),
            agent_mode="vision",
            default_actuator_speaker=StreamFormat(codec="pcm16le", sample_rate=16000, channels=1, chunk_ms=20),
            asset_request_timeout_seconds=3,
        )
    )
    runner, server_url = await _start_loopback_server(app)
    user_id = "user-split"
    device_id = "dev-split"
    ws_base = server_url.replace("http://", "ws://").replace("https://", "wss://")
    try:
        async with ClientSession() as session:
            control = await _register_raw_device(session, server_url, user_id=user_id, device_id=device_id)
            audio_input = await session.ws_connect(f"{ws_base}/ws/stream/audio/input?device_id={device_id}")
            visual_input = await session.ws_connect(f"{ws_base}/ws/stream/visual/input?device_id={device_id}")
            audio_output = await session.ws_connect(f"{ws_base}/ws/stream/audio/output?device_id={device_id}")

            await audio_input.send_bytes(
                _chunk(
                    user_id=user_id,
                    device_id=device_id,
                    stream_id="stream_mic_split",
                    stream_type="sensor.mic",
                    payload=b"\x00" * 640,
                    codec="pcm16le",
                )
            )
            await visual_input.send_bytes(
                _chunk(
                    user_id=user_id,
                    device_id=device_id,
                    stream_id="stream_rgb_split",
                    stream_type="sensor.rgb",
                    payload=b"\xff\xd8split-rgb\xff\xd9",
                    codec="jpeg",
                    sample_rate=1,
                    duration_ms=1,
                    metadata={"request_id": "req-split"},
                )
            )
            await audio_input.send_bytes(
                _chunk(
                    user_id=user_id,
                    device_id=device_id,
                    stream_id="stream_rgb_wrong_channel",
                    stream_type="sensor.rgb",
                    payload=b"\xff\xd8wrong-channel\xff\xd9",
                    codec="jpeg",
                    sample_rate=1,
                    duration_ms=1,
                )
            )
            error = await _receive_event(audio_input)
            assert error.event_name == "system.error.raised"
            assert "is not allowed on audio_input" in error.payload["message"]

            app.output_service.submit_audio(
                user_id=user_id,
                session_id=device_id,
                audio=b"\x01\x02" * 320,
                format=StreamFormat(codec="pcm16le", sample_rate=16000, channels=1, chunk_ms=20),
            )
            output_message = await audio_output.receive(timeout=2)
            assert output_message.type == WSMsgType.BINARY
            output_chunk = StreamChunkCodec.decode(output_message.data)
            assert output_chunk.stream_type == "actuator.speaker"
            assert output_chunk.payload

            await control.close()
            await audio_input.close()
            await visual_input.close()
            await audio_output.close()
    finally:
        await runner.cleanup()


def test_python_device_sdk_interoperates_with_real_server_sdk_websocket(tmp_path) -> None:
    """测试目标：验证真实 Server SDK 与 Python Device SDK 可以通过 WebSocket 闭环。

    测试方法：启动 `RealtimeAgentHttpServer`，用 `RealtimeAgentDeviceClient` 连接真实
    `/ws/control` 和 `/ws/stream`，依次触发 RGB 采集、设备命令和输出 stream。
    预期结果：server 获得 RGB asset 和 command.completed，device 收到 output
    stream chunk，并生成互操作测试报告。
    """

    asyncio.run(_run_loopback_contract(tmp_path))


def test_server_accepts_split_media_websockets(tmp_path) -> None:
    """测试目标：验证 server 已按协议拆分媒体 WebSocket。

    测试方法：启动真实 HTTP server，裸连接 `/ws/control` 完成注册，然后分别连接
    `/ws/stream/audio/input`、`/ws/stream/visual/input` 和 `/ws/stream/audio/output`。
    预期结果：麦克风 chunk 只能走音频上行，RGB chunk 只能走视觉上行，speaker chunk
    只能从音频下行收到；把 RGB 发到音频上行会返回协议错误。
    """

    asyncio.run(_run_split_stream_contract(tmp_path))
