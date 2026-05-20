import json
import os
import subprocess
import sys
from pathlib import Path

from aiohttp import web

from realtime_agent.app import RealtimeAgentApp, RealtimeAgentConfig
from realtime_agent.server import RealtimeAgentHttpServer


ROOT = Path(__file__).resolve().parents[4]
DEVICE_ROOT = ROOT / "examples/dev-support/devices/python-playback-glass"


def test_cli_register_only_over_real_websocket_server(tmp_path: Path) -> None:
    """测试目标：验证 pytest 只从外部 CLI 驱动回放端侧连接真实 WebSocket server。

    测试方法：测试进程只负责启动 aiohttp server；回放端侧通过子进程
    `python -m realtime_agent_python_playback_glass run` 执行 `register_only` Case。
    预期结果：CLI 退出码为 0，report.json 中 Case 通过。
    """

    import asyncio

    async def run() -> None:
        audio_app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
        server = RealtimeAgentHttpServer(audio_app)
        runner = web.AppRunner(server.create_web_app())
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001 - 测试随机端口需要读取 socket
        report_path = tmp_path / "report.json"
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            [
                str(ROOT / "audio-server"),
                str(ROOT / "examples/dev-support/devices/python-playback-glass"),
                env.get("PYTHONPATH", ""),
            ]
        )
        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                [
                    sys.executable,
                    "-m",
                    "realtime_agent_python_playback_glass",
                    "run",
                    "--server-url",
                    f"http://127.0.0.1:{port}",
                    "--case",
                    str(DEVICE_ROOT / "cases/smoke/register_only.yaml"),
                    "--runs-root",
                    str(tmp_path / "runs"),
                    "--report",
                    str(report_path),
                ],
                cwd=ROOT,
                env=env,
                check=False,
                text=True,
                capture_output=True,
                timeout=15,
            )
        finally:
            await runner.cleanup()
        assert completed.returncode == 0, completed.stderr + completed.stdout
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["ok"] is True
        assert report["cases"][0]["id"] == "register_only"

    asyncio.run(run())
