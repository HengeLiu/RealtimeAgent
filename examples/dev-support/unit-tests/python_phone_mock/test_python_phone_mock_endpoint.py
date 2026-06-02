from __future__ import annotations

import asyncio
from pathlib import Path

from aiohttp import ClientSession, web

from realtime_agent.app import RealtimeAgentApp, RealtimeAgentConfig
from realtime_agent_python_phone_mock.phone_mock import NetworkPythonPhoneMockEndpoint
from realtime_agent.protocol import SERVER_PRODUCER_ID, StreamChunk, StreamFormat, new_id
from realtime_agent.server import RealtimeAgentHttpServer


def test_python_phone_mock_registers_as_route_driven_endpoint(tmp_path: Path) -> None:
    """测试目标：验证 Python phone mock 通过 properties/route 注册为普通端侧。

    测试方法：启动真实 aiohttp server，使用 phone mock 走 `/ws/control` 完成注册。
    预期结果：debug snapshot 中能看到调试属性和订阅策略，且
    `client_type` 只是诊断字段，不参与固定 phone 类型路由。
    """

    async def run() -> None:
        audio_app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
        server = RealtimeAgentHttpServer(audio_app)
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
        assert snapshot["client_type"] == "python-phone"
        assert "phone.task.find_object_phone_task" not in snapshot["properties"]
        assert snapshot["device_id"] == "dev-phone"

    asyncio.run(run())


def test_python_phone_mock_uploads_rgb_and_consumes_haptic_stream(tmp_path: Path) -> None:
    """测试目标：验证 Python phone mock 同时支持传感器上传和执行器消费。

    测试方法：注册 phone mock 后，server 侧按订阅请求 `sensor.rgb` 资产，并打开
    `actuator.haptic` 输出 stream。
    预期结果：RGB 通过 stream 上传形成 AssetRef；haptic chunk 通过 stream 下行到
    phone mock；分发只由事件名和订阅过滤决定。
    """

    async def run() -> None:
        audio_app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
        server = RealtimeAgentHttpServer(audio_app)
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
                assert audio_app.asset_service.wait_for_archive(asset.asset_id, timeout_seconds=1)
                assert Path(asset.uri).read_bytes() == b"\xff\xd8phone-rgb\xff\xd9"

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
        assert "stream.control.open.requested" in received
        assert endpoint.asset_uploads and endpoint.asset_uploads[0]["payload_size"] > 0
        assert any(item["stream_type"] == "actuator.haptic" for item in endpoint.actuator_streams)
        assert all("target_device" not in event.payload for event in endpoint.received_events)

    asyncio.run(run())


def test_endpoint_reference_directories_exist() -> None:
    """测试目标：验证第 13 节要求的参考端侧目录已经建立。

    测试方法：检查 browser-glass、python-phone 和 iOS SDK Demo 目录中的最小配置。
    预期结果：每个端侧都有 README 或示例配置，便于后续并行小组继续实现。
    """

    root = Path(__file__).resolve().parents[4]
    assert (root / "examples/dev-support/devices/browser-glass/README.md").exists()
    assert (root / "examples/dev-support/devices/python-phone/phone.mock.yaml").exists()
    assert (root / "examples/device_app_demo/ios/DeviceDemo.xcodeproj/project.pbxproj").exists()
    assert (root / "examples/device_app_demo/agent-server/server.yaml").exists()


def test_python_phone_reference_uses_python_device_sdk() -> None:
    """测试目标：验证 Python phone 参考端已经迁移到端侧 SDK。

    测试方法：静态读取 phone mock 源码，检查它导入 `realtime_agent_device`，并且不再直接
    依赖 server 主包的 `realtime_agent.protocol`。
    预期结果：端侧事件和 stream chunk 模型来自 Python Device SDK，避免端侧绑定服务端内部包。
    """

    root = Path(__file__).resolve().parents[4]
    source_files = [
        root / "examples/dev-support/devices/python-glass/realtime_agent_python_glass/playback.py",
        root / "examples/dev-support/devices/python-phone/realtime_agent_python_phone_mock/phone_mock.py",
        root / "examples/dev-support/devices/python-phone/realtime_agent_python_phone_mock/playback_fallback.py",
        root / "examples/dev-support/devices/python-phone/realtime_agent_python_phone_mock/remote_task.py",
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in source_files)

    assert "from realtime_agent_device import" in source
    assert "realtime_agent.protocol" not in source
    assert "RealtimeAgentDeviceClient" in source
