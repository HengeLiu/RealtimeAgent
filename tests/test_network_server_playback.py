import asyncio
import json
from pathlib import Path

import pytest
from aiohttp import ClientSession, WSServerHandshakeError, web

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat_python_glass.playback import NetworkPythonPlaybackEndpoint
from audio_chat.protocol import Event, StreamFormat
from audio_chat.server import AudioChatHttpServer


def test_network_playback_over_real_websocket_transport(tmp_path: Path) -> None:
    """测试目标：验证 playback 通过真实 HTTP/WebSocket server 完成最小闭环。

    测试方法：在临时端口启动 aiohttp app，运行 `NetworkPythonPlaybackEndpoint`，
    通过控制 WebSocket 注册和唤醒，通过 stream WebSocket 上传/接收二进制 chunk。
    预期结果：事件链完整，输出 chunk 非空，runs 目录写出 playback-result。
    """

    async def run() -> None:
        audio_app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
        server = AudioChatHttpServer(audio_app)
        app = server.create_web_app()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        sockets = site._server.sockets  # noqa: SLF001 - aiohttp 测试场景需要读取临时端口
        port = sockets[0].getsockname()[1]
        try:
            endpoint = NetworkPythonPlaybackEndpoint(
                server_url=f"http://127.0.0.1:{port}",
                user_id="user-net",
                device_id="dev-net",
                runs_root=str(tmp_path / "runs"),
            )
            result = await endpoint.run_once()
        finally:
            await runner.cleanup()

        assert result["passed"] is True
        assert result["transport"] == "network"
        assert result["output_chunk_count"] > 0
        session_dir = tmp_path / "runs" / "sessions" / result["session_id"]
        assert (session_dir / "events.jsonl").exists()
        assert (session_dir / "stream-events.jsonl").exists()
        assert (session_dir / "agent-events.jsonl").exists()
        assert (session_dir / "playback-decisions.jsonl").exists()
        assert (session_dir / "playback-result.json").exists()
        assert (session_dir / "result.json").exists()

    asyncio.run(run())


def test_network_playback_streams_recorded_wav_chunks(tmp_path: Path) -> None:
    """测试目标：验证网络模式 python-glass 会按真实协议分片上传录制 WAV。

    测试方法：启动真实 aiohttp server，使用 `NetworkPythonPlaybackEndpoint` 上传
    老 SDK 的 WAV 样例。
    预期结果：回放通过，input PCM 落盘内容与原 WAV 数据区一致，且 chunk 数大于 1。
    """

    async def run() -> None:
        wav_path = Path("legacy/openaiglass-sdk/testdata/audio-sample/wav/看一下我前面有什么.wav")
        audio_app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
        server = AudioChatHttpServer(audio_app)
        app = server.create_web_app()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001
        try:
            endpoint = NetworkPythonPlaybackEndpoint(
                server_url=f"http://127.0.0.1:{port}",
                user_id="user-net-wav",
                device_id="dev-net-wav",
                runs_root=str(tmp_path / "runs"),
            )
            from audio_chat_python_glass.playback import load_wav_audio

            audio = load_wav_audio(wav_path)
            result = await endpoint.run_once(audio=audio)
        finally:
            await runner.cleanup()

        assert result["passed"] is True
        assert result["transport"] == "network"
        assert result["input_audio"]["chunk_count"] > 1
        session_dir = tmp_path / "runs" / "sessions" / result["session_id"]
        input_pcm = b"".join(path.read_bytes() for path in session_dir.glob("input-*.pcm"))
        import wave

        with wave.open(audio.source_path, "rb") as wav_file:
            expected_pcm = wav_file.readframes(wav_file.getnframes())
        assert input_pcm == expected_pcm

    asyncio.run(run())


def test_network_multi_device_subscription_routes_rgb_and_speaker(tmp_path: Path) -> None:
    """测试目标：验证同一 user_id 下多设备按 capability/subscription 分发。

    测试方法：启动真实 aiohttp server，注册一台只产 `sensor.rgb` 的设备和一台只消费
    `actuator.speaker` 的设备；server 侧分别触发资产请求和播报输出。
    预期结果：RGB 控制请求只到相机设备，speaker chunk 只到播放器设备，不依赖固定设备类型。
    """

    async def run() -> None:
        audio_app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
        server = AudioChatHttpServer(audio_app)
        runner = web.AppRunner(server.create_web_app())
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001
        server_url = f"http://127.0.0.1:{port}"
        user_id = "user-multi"
        session_id = "sess-multi"
        rgb = NetworkPythonPlaybackEndpoint(
            server_url=server_url,
            user_id=user_id,
            device_id="dev-rgb-only",
            runs_root=str(tmp_path / "runs"),
            device_name="rgb-only",
            client_type="python-mock-rgb",
            properties={"camera.role": "rgb-only"},
            subscriptions=[
                {"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}},
            ],
        )
        speaker = NetworkPythonPlaybackEndpoint(
            server_url=server_url,
            user_id=user_id,
            device_id="dev-speaker-only",
            runs_root=str(tmp_path / "runs"),
            device_name="speaker-only",
            client_type="python-mock-speaker",
            properties={"speaker.role": "speaker-only"},
            subscriptions=[
                {"event": "stream.output.*", "filter": {"stream_type": "actuator.speaker"}},
            ],
        )
        tasks = []
        try:
            async with ClientSession() as session:
                rgb_control = await rgb.run_until_registered(session=session)
                speaker_control = await speaker.run_until_registered(session=session)
                rgb_stream = await session.ws_connect(rgb._stream_url())  # noqa: SLF001 - 端侧参考测试复用网络端点内部 URL
                speaker_stream = await session.ws_connect(speaker._stream_url())  # noqa: SLF001
                tasks.extend(
                    [
                        asyncio.create_task(rgb._control_loop(rgb_control, rgb_stream, None)),  # noqa: SLF001
                        asyncio.create_task(rgb._stream_loop(rgb_control, rgb_stream)),  # noqa: SLF001
                        asyncio.create_task(speaker._control_loop(speaker_control, speaker_stream, None)),  # noqa: SLF001
                        asyncio.create_task(speaker._stream_loop(speaker_control, speaker_stream)),  # noqa: SLF001
                    ]
                )
                asset = await asyncio.to_thread(
                    audio_app.asset_service.request_asset,
                    user_id=user_id,
                    stream_type="sensor.rgb",
                    freshness_seconds=0,
                    session_id=session_id,
                    timeout_seconds=2,
                )
                assert asset is not None
                for _ in range(20):
                    if rgb.asset_uploads:
                        break
                    await asyncio.sleep(0.02)
                audio_app.output_service.submit_text(user_id=user_id, session_id=session_id, text="multi device route")
                await asyncio.wait_for(speaker._output_closed.wait(), timeout=5)  # noqa: SLF001
                await rgb_control.close()
                await speaker_control.close()
                await rgb_stream.close()
                await speaker_stream.close()
        finally:
            for task in tasks:
                task.cancel()
            await runner.cleanup()

        rgb_received = [event.event_name for event in rgb.received_events]
        speaker_received = [event.event_name for event in speaker.received_events]
        assert "stream.control.configure.requested" in rgb_received
        assert "stream.control.configure.requested" not in speaker_received
        assert rgb.asset_uploads and rgb.asset_uploads[0]["payload_size"] > 0
        assert speaker.output_chunks
        assert not rgb.output_chunks
        snapshot = audio_app.control_service.build_user_snapshot(user_id)
        assert {device["device_id"] for device in snapshot["devices"]} == {"dev-rgb-only", "dev-speaker-only"}

    asyncio.run(run())


def test_network_playback_static_token_auth(tmp_path: Path) -> None:
    """测试目标：验证 `auth.mode=static_token` 在网络 playback 注册路径生效。

    测试方法：server 配置静态 token，endpoint 注册 payload 携带同一 token。
    预期结果：设备注册成功，最小 playback 闭环通过。
    """

    async def run() -> None:
        audio_app = AudioChatApp(
            AudioChatConfig(
                runs_root=str(tmp_path / "runs"),
                auth_mode="static_token",
                device_tokens={"dev-token": "token-001"},
                default_actuator_speaker=StreamFormat(sample_rate=16000, chunk_ms=40),
            )
        )
        server = AudioChatHttpServer(audio_app)
        runner = web.AppRunner(server.create_web_app())
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001
        try:
            endpoint = NetworkPythonPlaybackEndpoint(
                server_url=f"http://127.0.0.1:{port}",
                user_id="user-token",
                device_id="dev-token",
                runs_root=str(tmp_path / "runs"),
                auth={"mode": "static_token", "token": "token-001"},
            )
            result = await endpoint.run_once()
        finally:
            await runner.cleanup()

        assert result["passed"] is True

    asyncio.run(run())


def test_failed_network_registration_does_not_allow_stream_connection(tmp_path: Path) -> None:
    """测试目标：验证注册失败不会留下可建流的网络连接。

    测试方法：启动静态 token 鉴权的真实 aiohttp server，端侧使用错误 token
    发送注册事件，然后继续尝试用同一 device_id 连接 `/ws/stream`。
    预期结果：注册响应为 `control.device.register.failed`，stream WebSocket
    握手返回 404，server 连接表中不会保留该 device_id。
    """

    async def run() -> None:
        audio_app = AudioChatApp(
            AudioChatConfig(
                runs_root=str(tmp_path / "runs"),
                auth_mode="static_token",
                device_tokens={"dev-token-denied": "token-ok"},
            )
        )
        server = AudioChatHttpServer(audio_app)
        runner = web.AppRunner(server.create_web_app())
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001
        server_url = f"http://127.0.0.1:{port}"
        try:
            async with ClientSession() as session:
                control_ws = await session.ws_connect(f"{server_url}/ws/control")
                await control_ws.send_str(
                    json.dumps(
                        Event(
                            event_name="control.device.register.requested",
                            user_id="user-token-denied",
                            producer_id="dev-token-denied",
                            payload={
                                "device_id": "dev-token-denied",
                                "device_name": "denied-playback",
                                "client_type": "python-playback",
                                "sdk_version": "audio-chat-test",
                                "auth": {"mode": "static_token", "token": "token-bad"},
                                "subscriptions": [{"event": "stream.output.*"}],
                            },
                        ).to_dict(),
                        ensure_ascii=False,
                    )
                )
                message = await control_ws.receive_json()
                assert message["event_name"] == "control.device.register.failed"
                with pytest.raises(WSServerHandshakeError) as exc_info:
                    await session.ws_connect(f"{server_url}/ws/stream?device_id=dev-token-denied")
                assert exc_info.value.status == 404
                await control_ws.close()
        finally:
            await runner.cleanup()

        assert "dev-token-denied" not in server.connections

    asyncio.run(run())
