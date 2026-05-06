import asyncio
from pathlib import Path

from aiohttp import web

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.endpoints.python_playback import NetworkPythonPlaybackEndpoint
from audio_chat.protocol import StreamFormat
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
