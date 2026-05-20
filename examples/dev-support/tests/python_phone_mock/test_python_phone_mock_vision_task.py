from __future__ import annotations

import asyncio
from pathlib import Path

from aiohttp import ClientSession, web

from realtime_agent.app import RealtimeAgentApp, RealtimeAgentConfig
from realtime_agent_python_phone_mock.phone_mock import NetworkPythonPhoneMockEndpoint, PhoneTaskHandlerRegistry
from realtime_agent.protocol import Event
from realtime_agent.server import RealtimeAgentHttpServer


def test_python_phone_mock_does_not_register_legacy_business_task_handlers_by_default() -> None:
    """测试目标：验证 Python phone mock 默认不内置旧业务任务 handler。

    测试方法：创建默认注册表，读取 task_type 列表。
    预期结果：默认注册表为空；业务视觉任务应通过 peer video 命令或显式
    handler package 扩展，不在开发支持组件里硬编码旧 `phone.task.start` 业务。
    """

    registry = PhoneTaskHandlerRegistry.with_builtins()

    assert "find_object_phone_task" not in registry.list_task_types()
    assert "traffic_light_phone_task" not in registry.list_task_types()


def test_python_phone_mock_rejects_legacy_phone_task_start_without_business_handler(tmp_path: Path) -> None:
    """测试目标：验证 phone mock 对未显式注册的旧业务任务给出协议失败。

    测试方法：启动真实 aiohttp server，注册 Python phone mock，然后直接向该设备
    投递旧 `phone.task.start/find_object_phone_task` 命令。
    预期结果：phone mock 通过 `command.failed` 明确回报 unknown handler，不伪造
    业务执行结果，也不把旧 task 重新塞进 Server SDK 或示例应用 TaskRegistry。
    """

    async def run() -> None:
        audio_app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
        server = RealtimeAgentHttpServer(audio_app)
        runner = web.AppRunner(server.create_web_app())
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001
        endpoint = NetworkPythonPhoneMockEndpoint(
            server_url=f"http://127.0.0.1:{port}",
            user_id="user-phone-task",
            device_id="dev-python-phone-task",
            runs_root=str(tmp_path / "runs"),
            vision_frames={
                "find_object_phone_task": [b"\xff\xd8find-frame-1\xff\xd9", b"\xff\xd8find-frame-2\xff\xd9"],
                "traffic_light_phone_task": [b"\xff\xd8traffic-frame\xff\xd9"],
            },
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
                audio_app.control_service._push_event_to_device_ids(  # noqa: SLF001 - 系统级测试需要向指定参考端注入控制事件
                    Event(
                        event_name="command.requested",
                        user_id="user-phone-task",
                        producer_id="server-main",
                        session_id="dev-python-phone-task",
                        payload={
                            "command_id": "cmd-legacy-find-object",
                            "command": "phone.task.start",
                            "params": {"task_type": "find_object_phone_task", "task_id": "task-legacy-find-object"},
                        },
                    ),
                    ("dev-python-phone-task",),
                )
                await _wait_for_command_event(endpoint, "command.failed")
                await control_ws.close()
                await stream_ws.close()
        finally:
            for task in tasks:
                task.cancel()
            await runner.cleanup()

        assert endpoint.task_command_events
        failed = endpoint.task_command_events[-1]
        assert failed["event_name"] == "command.failed"
        assert failed["task_type"] == "find_object_phone_task"
        assert "unknown phone task handler" in failed["message"]
        assert endpoint.frame_log == []

    asyncio.run(run())


async def _wait_for_command_event(endpoint: NetworkPythonPhoneMockEndpoint, event_name: str) -> None:
    """等待 phone mock 上报指定命令事件。"""

    deadline = asyncio.get_running_loop().time() + 3
    while asyncio.get_running_loop().time() < deadline:
        if any(item["event_name"] == event_name for item in endpoint.task_command_events):
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"phone mock did not emit {event_name}")
