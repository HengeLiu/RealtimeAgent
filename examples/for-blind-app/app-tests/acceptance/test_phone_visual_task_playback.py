from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from aiohttp import ClientSession, web

from realtime_agent.app import RealtimeAgentApp, RealtimeAgentConfig
from realtime_agent.errors import RealtimeAgentError
from realtime_agent_python_phone_mock.phone_mock import NetworkPythonPhoneMockEndpoint
from realtime_agent.server import RealtimeAgentHttpServer


FOR_BLIND_APP_ROOT = Path(__file__).resolve().parents[4] / "examples" / "for-blind-app" / "audio-server"


def test_legacy_phone_visual_tasks_are_not_registered_in_peer_video_app(tmp_path: Path) -> None:
    """测试目标：确认旧 phone visual task 名称不再作为应用 Task 注册。

    测试方法：以真实 WebSocket server 注册 Python phone mock，然后尝试创建旧
    `find_object_phone_task`。
    预期结果：TaskEngine 明确返回 unknown task；当前主线使用
    `find_object_task` / `traffic_light_task` 编排 peer video，而不是旧
    `phone.task.start` 业务任务。
    """

    async def run() -> None:
        sys.path = [path for path in sys.path if path != str(FOR_BLIND_APP_ROOT)]
        sys.path.insert(0, str(FOR_BLIND_APP_ROOT))
        for name in list(sys.modules):
            if name == "capabilities" or name.startswith("capabilities."):
                sys.modules.pop(name, None)

        app = RealtimeAgentApp(
            RealtimeAgentConfig(
                runs_root=str(tmp_path / "runs"),
                tasks_discover_enabled=True,
                tasks_discover_packages=("capabilities",),
                tasks_discover_recursive=True,
            )
        )
        server = RealtimeAgentHttpServer(app)
        runner = web.AppRunner(server.create_web_app())
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001
        endpoint = NetworkPythonPhoneMockEndpoint(
            server_url=f"http://127.0.0.1:{port}",
            user_id="user-accept-phone",
            device_id="dev-accept-phone",
            runs_root=str(tmp_path / "runs"),
            vision_frames={
                "*": [b"\xff\xd8accept-phone-frame-1\xff\xd9", b"\xff\xd8accept-phone-frame-2\xff\xd9"],
            },
        )
        loop_tasks: list[asyncio.Task] = []
        try:
            async with ClientSession() as session:
                control_ws = await endpoint.run_until_registered(session=session)
                stream_ws = await session.ws_connect(endpoint._stream_url())  # noqa: SLF001
                loop_tasks.extend(
                    [
                        asyncio.create_task(endpoint._control_loop(control_ws, stream_ws, None)),  # noqa: SLF001
                        asyncio.create_task(endpoint._stream_loop(control_ws, stream_ws)),  # noqa: SLF001
                    ]
                )
                try:
                    await app.task_engine.create(
                        task_type="find_object_phone_task",
                        user_id="user-accept-phone",
                        session_id="sess-accept-phone",
                        input_data={"target": "手机"},
                    )
                except RealtimeAgentError as exc:
                    assert "unknown task: find_object_phone_task" in str(exc)
                else:
                    raise AssertionError("legacy find_object_phone_task should not be registered")
                await control_ws.close()
                await stream_ws.close()
        finally:
            for task in loop_tasks:
                task.cancel()
            await runner.cleanup()

        assert "find_object_phone_task" not in app.task_engine.registry.list_task_types()
        assert "traffic_light_phone_task" not in app.task_engine.registry.list_task_types()
        assert {"find_object_task", "traffic_light_task"} <= set(app.task_engine.registry.list_task_types())
        assert endpoint.frame_log == []

    asyncio.run(run())
