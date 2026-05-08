from __future__ import annotations

import asyncio
from pathlib import Path

from aiohttp import ClientSession, web

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat_python_phone_mock.phone_mock import NetworkPythonPhoneMockEndpoint
from audio_chat.protocol import SERVER_PRODUCER_ID, StreamChunk, StreamFormat, new_id
from audio_chat.server import AudioChatHttpServer


def test_python_phone_mock_registers_as_capability_driven_endpoint(tmp_path: Path) -> None:
    """测试目标：验证 Python phone mock 通过 capability/subscription 注册为普通端侧。

    测试方法：启动真实 aiohttp server，使用 phone mock 走 `/ws/control` 完成注册。
    预期结果：debug snapshot 中能看到它生产 sensor.rgb、消费 speaker/haptic，且
    `client_type` 只是诊断字段，不参与固定 phone 类型路由。
    """

    async def run() -> None:
        audio_app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
        server = AudioChatHttpServer(audio_app)
        runner = web.AppRunner(server.create_web_app())
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001
        try:
            endpoint = NetworkPythonPhoneMockEndpoint(
                server_url=f"http://127.0.0.1:{port}",
                user_id="user-phone",
                device_id="dev-phone",
                runs_root=str(tmp_path / "runs"),
            )
            async with ClientSession() as session:
                control_ws = await endpoint.run_until_registered(session=session)
                await control_ws.close()
        finally:
            await runner.cleanup()

        snapshot = audio_app.control_service.build_device_snapshot("dev-phone")
        assert snapshot is not None
        assert snapshot["client_type"] == "python-phone-mock"
        assert "sensor.rgb" in snapshot["capabilities"]["streams.produce"]
        assert "actuator.speaker" in snapshot["capabilities"]["streams.consume"]
        assert "actuator.haptic" in snapshot["capabilities"]["streams.consume"]

    asyncio.run(run())


def test_python_phone_mock_uploads_rgb_and_consumes_haptic_stream(tmp_path: Path) -> None:
    """测试目标：验证 Python phone mock 同时支持传感器上传和执行器消费。

    测试方法：注册 phone mock 后，server 侧按能力请求 `sensor.rgb` 资产，并打开
    `actuator.haptic` 输出 stream。
    预期结果：RGB 通过 stream 上传形成 AssetRef；haptic chunk 通过 stream 下行到
    phone mock；分发只由订阅和 capability 决定。
    """

    async def run() -> None:
        audio_app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
        server = AudioChatHttpServer(audio_app)
        runner = web.AppRunner(server.create_web_app())
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001
        endpoint = NetworkPythonPhoneMockEndpoint(
            server_url=f"http://127.0.0.1:{port}",
            user_id="user-phone",
            device_id="dev-phone",
            runs_root=str(tmp_path / "runs"),
            rgb_payload=b"\xff\xd8phone-rgb\xff\xd9",
        )
        tasks: list[asyncio.Task] = []
        try:
            async with ClientSession() as session:
                control_ws = await endpoint.run_until_registered(session=session)
                stream_ws = await session.ws_connect(endpoint._stream_url())  # noqa: SLF001
                tasks.extend(
                    [
                        asyncio.create_task(endpoint._control_loop(control_ws, stream_ws, None)),  # noqa: SLF001
                        asyncio.create_task(endpoint._stream_loop(control_ws, stream_ws)),  # noqa: SLF001
                    ]
                )
                asset = await asyncio.to_thread(
                    audio_app.asset_service.request_asset,
                    user_id="user-phone",
                    stream_type="sensor.rgb",
                    freshness_seconds=0,
                    session_id="sess-phone",
                    timeout_seconds=2,
                )
                assert asset is not None
                assert asset.stream_type == "sensor.rgb"
                assert Path(asset.path).read_bytes() == b"\xff\xd8phone-rgb\xff\xd9"

                handle = audio_app.stream_service.open_stream(
                    user_id="user-phone",
                    session_id="sess-phone",
                    stream_type="actuator.haptic",
                    producer_id=SERVER_PRODUCER_ID,
                    format=StreamFormat(codec="raw", sample_rate=1, channels=1, chunk_ms=50),
                )
                audio_app.stream_service.write_chunk(
                    StreamChunk(
                        user_id="user-phone",
                        session_id="sess-phone",
                        stream_id=handle.stream_id,
                        stream_type="actuator.haptic",
                        seq=0,
                        payload=b"\x01\x02",
                        codec="raw",
                        sample_rate=1,
                        channels=1,
                        duration_ms=50,
                        final=True,
                    )
                )
                audio_app.stream_service.close_stream(handle.stream_id, reason="test_haptic_done")
                await asyncio.wait_for(endpoint._output_closed.wait(), timeout=3)  # noqa: SLF001
                await control_ws.close()
                await stream_ws.close()
        finally:
            for task in tasks:
                task.cancel()
            await runner.cleanup()

        received = [event.event_name for event in endpoint.received_events]
        assert "stream.control.configure.requested" in received
        assert endpoint.asset_uploads and endpoint.asset_uploads[0]["payload_size"] > 0
        assert any(item["stream_type"] == "actuator.haptic" for item in endpoint.actuator_streams)
        assert all("target_device" not in event.payload for event in endpoint.received_events)

    asyncio.run(run())


def test_endpoint_reference_directories_exist() -> None:
    """测试目标：验证第 13 节要求的参考端侧目录已经建立。

    测试方法：检查 web-glass、python-phone-mock、iOS 和 ESP32-S3 目录中的最小配置。
    预期结果：每个端侧都有 README 或示例配置，便于后续并行小组继续实现。
    """

    root = Path(__file__).resolve().parents[1]
    assert (root / "endpoints-examples/web-glass/README.md").exists()
    assert (root / "endpoints-examples/python-phone-mock/phone.mock.yaml").exists()
    assert (root / "endpoints-examples/ios-phone/AppConfig.example.json").exists()
    assert (root / "endpoints-examples/esp32-s3/local.env.example").exists()
