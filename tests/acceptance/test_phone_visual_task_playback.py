from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from aiohttp import ClientSession, web

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat_python_phone_mock.phone_mock import NetworkPythonPhoneMockEndpoint
from audio_chat.server import AudioChatHttpServer


BASIC_APP_ROOT = Path(__file__).resolve().parents[2] / "app-examples" / "basic-app"


def test_find_object_and_traffic_light_phone_visual_tasks_playback(tmp_path: Path) -> None:
    """测试目标：验收 D 线 phone mock 视觉任务设备级闭环。

    测试方法：以真实 WebSocket server 注册 Python phone mock，创建 find_object 和
    traffic_light 两个迁移样板 Task，让 mock 通过 command 事件和 sensor.rgb stream
    回报任务结果。
    预期结果：两个任务完成，RGB 帧不进入控制 payload，runs 产物记录 phone_task 和
    task.completed 事件。
    """

    async def run() -> None:
        if str(BASIC_APP_ROOT) not in sys.path:
            sys.path.insert(0, str(BASIC_APP_ROOT))
        for name in list(sys.modules):
            if name == "capabilities" or name.startswith("capabilities."):
                sys.modules.pop(name, None)

        app = AudioChatApp(
            AudioChatConfig(
                runs_root=str(tmp_path / "runs"),
                tasks_discover_enabled=True,
                tasks_discover_packages=("capabilities",),
                tasks_discover_recursive=True,
            )
        )
        server = AudioChatHttpServer(app)
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
                find_ref = await app.task_engine.create(
                    task_type="find_object_phone_task",
                    user_id="user-accept-phone",
                    session_id="sess-accept-phone",
                    input_data={"target": "手机"},
                )
                traffic_ref = await app.task_engine.create(
                    task_type="traffic_light_phone_task",
                    user_id="user-accept-phone",
                    session_id="sess-accept-phone",
                    input_data={"expected_color": "green"},
                )
                await _wait_for_completed(app, find_ref.task_id)
                await _wait_for_completed(app, traffic_ref.task_id)
                await control_ws.close()
                await stream_ws.close()
        finally:
            for task in loop_tasks:
                task.cancel()
            await runner.cleanup()

        assert app.task_engine.query(find_ref.task_id).state == "completed"
        assert app.task_engine.query(traffic_ref.task_id).state == "completed"
        assert len(endpoint.frame_log) >= 4
        control_payload_text = "\n".join(event.to_dict()["payload"].__repr__() for event in endpoint.sent_events)
        assert "image_base64" not in control_payload_text
        assert "raw_bytes" not in control_payload_text
        task_events = (tmp_path / "runs/sessions/sess-accept-phone/task-events.jsonl").read_text(encoding="utf-8")
        assert "phone_task.started" in task_events
        assert "phone_task.progress" in task_events
        assert "phone_task.completed" in task_events
        assert "task.completed" in task_events

    asyncio.run(run())


async def _wait_for_completed(app: AudioChatApp, task_id: str) -> None:
    deadline = asyncio.get_running_loop().time() + 3
    while asyncio.get_running_loop().time() < deadline:
        if app.task_engine.query(task_id).state == "completed":
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"task {task_id} did not complete")
