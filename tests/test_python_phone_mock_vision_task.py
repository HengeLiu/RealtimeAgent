from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from aiohttp import ClientSession, web

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat_python_phone_mock.phone_mock import NetworkPythonPhoneMockEndpoint, PhoneTaskHandlerRegistry
from audio_chat.server import AudioChatHttpServer


FOR_BLIND_APP_ROOT = Path(__file__).resolve().parents[1] / "app-examples" / "for-blind-app"


def test_python_phone_mock_discovers_builtin_vision_task_handlers() -> None:
    """测试目标：验证 Python phone mock 具备端侧任务 handler 注册表。

    测试方法：创建内置注册表，读取 task_type 列表。
    预期结果：包含 find_object 和 traffic_light 两个老业务迁移样板 handler。
    """

    registry = PhoneTaskHandlerRegistry.with_builtins()

    assert "find_object_phone_task" in registry.list_task_types()
    assert "traffic_light_phone_task" in registry.list_task_types()


def test_python_phone_mock_executes_find_object_and_traffic_light_tasks(tmp_path: Path) -> None:
    """测试目标：验证 phone mock 可作为独立设备执行视觉任务。

    测试方法：启动真实 aiohttp server，注册 Python phone mock，然后通过 for-blind-app
    TaskEngine 创建 `find_object_phone_task` 和 `traffic_light_phone_task`。
    预期结果：phone mock 收到 command 事件、上传 RGB stream、上报 completed，两个
    server 侧任务都进入 completed。
    """

    async def run() -> None:
        sys.path = [path for path in sys.path if path != str(FOR_BLIND_APP_ROOT)]
        sys.path.insert(0, str(FOR_BLIND_APP_ROOT))
        for name in list(sys.modules):
            if name == "capabilities" or name.startswith("capabilities."):
                sys.modules.pop(name, None)

        audio_app = AudioChatApp(
            AudioChatConfig(
                runs_root=str(tmp_path / "runs"),
                tasks_discover_enabled=True,
                tasks_discover_packages=("capabilities",),
                tasks_discover_recursive=True,
                tools_discover_enabled=True,
                tools_discover_packages=("capabilities",),
                tools_discover_recursive=True,
            )
        )
        server = AudioChatHttpServer(audio_app)
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
                find_ref = await audio_app.task_engine.create(
                    task_type="find_object_phone_task",
                    user_id="user-phone-task",
                    session_id="dev-python-phone-task",
                    input_data={"target": "水杯"},
                )
                traffic_ref = await audio_app.task_engine.create(
                    task_type="traffic_light_phone_task",
                    user_id="user-phone-task",
                    session_id="dev-python-phone-task",
                    input_data={"expected_color": "green"},
                )
                await _wait_for_state(audio_app, find_ref.task_id, "completed")
                await _wait_for_state(audio_app, traffic_ref.task_id, "completed")
                await control_ws.close()
                await stream_ws.close()
        finally:
            for task in tasks:
                task.cancel()
            await runner.cleanup()

        assert audio_app.task_engine.query(find_ref.task_id).state == "completed"
        assert audio_app.task_engine.query(traffic_ref.task_id).state == "completed"
        assert [event["event_name"] for event in endpoint.task_events].count("command.completed") == 2
        assert any(item["task_type"] == "find_object_phone_task" for item in endpoint.frame_log)
        assert any(item["task_type"] == "traffic_light_phone_task" for item in endpoint.frame_log)
        task_events = (tmp_path / "runs/user-phone-task/dev-python-phone-task/task-events.jsonl").read_text(encoding="utf-8")
        assert "phone_task.completed" in task_events
        assert "task.completed" in task_events
        assert "水杯" in task_events

    asyncio.run(run())


async def _wait_for_state(audio_app: AudioChatApp, task_id: str, state: str) -> None:
    """等待任务进入目标状态。"""

    deadline = asyncio.get_running_loop().time() + 3
    while asyncio.get_running_loop().time() < deadline:
        if audio_app.task_engine.query(task_id).state == state:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"task {task_id} did not reach {state}")
