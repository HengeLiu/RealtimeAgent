from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path

from audio_chat import AudioChatApp, AudioChatConfig
from audio_chat.protocol import Event


APP_ROOT = Path(__file__).resolve().parents[1] / "audio-server"


class RecordingEndpoint:
    """记录控制事件的测试端侧。"""

    def __init__(self, *, user_id: str, device_id: str) -> None:
        self.user_id = user_id
        self.device_id = device_id
        self.events: list[Event] = []

    def push_event(self, event: Event) -> None:
        """记录 server 投递到端侧的控制事件。"""

        self.events.append(event)

    def push_stream_chunk(self, chunk: object) -> None:
        """测试不处理 stream chunk。"""

        _ = chunk

    def close(self, *, reason: str) -> None:
        """测试不需要处理连接关闭。"""

        _ = reason


def test_find_object_task_orchestrates_phone_then_glass(tmp_path: Path) -> None:
    """测试目标：验证找物 Task 按 phone receiver -> glass sender 的顺序编排。

    测试方法：注册两台 command 设备，后台启动 `find_object_task`，手动发布 phone
    ready、glass connected 和 phone completed 回执。
    预期结果：phone 先收到 `peer.video.receiver.start`，glass 后收到
    `peer.video.sender.start`，任务最终 completed 且不再请求 `sensor.rgb.one()`。
    """

    async def run() -> None:
        app = _app_with_peer_tasks(tmp_path)
        phone = RecordingEndpoint(user_id="user-peer", device_id="dev-phone")
        glass = RecordingEndpoint(user_id="user-peer", device_id="dev-glass")
        _register_command_endpoint(app, phone, properties={"device_role": "phone", "audio_chat.audio_output": "actuator.speaker"})
        _register_command_endpoint(app, glass, properties={"device_role": "glass", "audio_chat.audio_output": "actuator.speaker"})

        create_task = asyncio.create_task(
            app.task_engine.create(
                task_type="find_object_task",
                user_id="user-peer",
                session_id="dev-glass",
                input_data={"object_name": "水杯", "timeout_seconds": 1},
            )
        )
        phone_command = await _wait_for_command(phone)
        assert phone_command.payload["command"] == "peer.video.receiver.start"
        assert not _command_events(glass)
        phone_cmd = phone_command.payload["command_id"]
        app.publish_control_event(
            Event(
                event_name="command.progress",
                user_id="user-peer",
                producer_id="dev-phone",
                session_id="dev-phone",
                payload={
                    "command_id": phone_cmd,
                    "command": "peer.video.receiver.start",
                    "status": "peer.receiver.ready",
                    "data": {"receiver": {"transport": "websocket", "url": "ws://127.0.0.1:19081/peer-video/task"}},
                },
            )
        )
        glass_command = await _wait_for_command(glass)
        assert glass_command.payload["command"] == "peer.video.sender.start"
        assert glass_command.payload["params"]["receiver"]["url"].startswith("ws://")
        glass_cmd = glass_command.payload["command_id"]
        app.publish_control_event(
            Event(
                event_name="command.progress",
                user_id="user-peer",
                producer_id="dev-glass",
                session_id="dev-glass",
                payload={
                    "command_id": glass_cmd,
                    "command": "peer.video.sender.start",
                    "status": "peer.sender.connected",
                },
            )
        )
        app.publish_control_event(
            Event(
                event_name="command.completed",
                user_id="user-peer",
                producer_id="dev-phone",
                session_id="dev-phone",
                payload={
                    "command_id": phone_cmd,
                    "command": "peer.video.receiver.start",
                    "result": {
                        "type": "find_object",
                        "object_name": "水杯",
                        "found": True,
                        "confidence": 0.76,
                        "message": "已找到水杯，位于前方",
                        "source": "mock",
                    },
                },
            )
        )
        ref = await asyncio.wait_for(create_task, timeout=2)
        assert ref.state == "completed"
        assert app.task_engine.query(ref.task_id).summary == "找物完成"

    asyncio.run(run())


def test_traffic_light_task_reports_green_with_high_priority_path(tmp_path: Path) -> None:
    """测试目标：验证红绿灯 Task 使用 peer video result 完成并生成 green 信号。

    测试方法：注册 phone/glass 设备，驱动 receiver ready、sender connected 和 phone
    completed green result。
    预期结果：任务 completed，运行产物中包含 `traffic_light.green` 信号。
    """

    async def run() -> None:
        app = _app_with_peer_tasks(tmp_path)
        phone = RecordingEndpoint(user_id="user-peer", device_id="dev-phone")
        glass = RecordingEndpoint(user_id="user-peer", device_id="dev-glass")
        _register_command_endpoint(app, phone, properties={"device_role": "phone", "audio_chat.audio_output": "actuator.speaker"})
        _register_command_endpoint(app, glass, properties={"device_role": "glass", "audio_chat.audio_output": "actuator.speaker"})
        create_task = asyncio.create_task(
            app.task_engine.create(
                task_type="traffic_light_task",
                user_id="user-peer",
                session_id="dev-glass",
                input_data={"timeout_seconds": 1},
            )
        )
        await _drive_successful_peer_video(app, phone, glass, {"type": "traffic_light", "state": "green", "can_cross": True, "message": "绿灯，可以在确认安全后通行", "source": "mock"})
        ref = await asyncio.wait_for(create_task, timeout=2)
        assert ref.state == "completed"
        task_signals = (tmp_path / "runs/user-peer/dev-glass/task-signals.jsonl").read_text(encoding="utf-8")
        assert "traffic_light.green" in task_signals

    asyncio.run(run())


def _app_with_peer_tasks(tmp_path: Path) -> AudioChatApp:
    """创建注册了 for-blind peer video Task 的测试 app。"""

    sys.path = [path for path in sys.path if path != str(APP_ROOT)]
    sys.path.insert(0, str(APP_ROOT))
    for name in list(sys.modules):
        if name == "capabilities" or name.startswith("capabilities."):
            sys.modules.pop(name, None)
    tasks_module = importlib.import_module("capabilities.tasks")
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    app.task_engine.register(tasks_module.FindObjectTask)
    app.task_engine.register(tasks_module.TrafficLightTask)
    return app


def _register_command_endpoint(app: AudioChatApp, endpoint: RecordingEndpoint, *, properties: dict) -> None:
    """注册一台支持 command.* 的测试端侧。"""

    response = app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id=endpoint.user_id,
            producer_id=endpoint.device_id,
            payload={
                "device_id": endpoint.device_id,
                "device_name": endpoint.device_id,
                "client_type": "peer-video-test",
                "sdk_version": "audio-chat-test",
                "auth": {"mode": "disabled"},
                "supports": {"sensors": [], "actuators": [{"type": "vibrator"}]},
                "properties": properties,
            },
        ),
        endpoint,
    )
    assert response.event_name == "control.device.registered"


async def _wait_for_events(endpoint: RecordingEndpoint, count: int) -> None:
    """等待端侧收到指定数量控制事件。"""

    deadline = asyncio.get_running_loop().time() + 1
    while asyncio.get_running_loop().time() < deadline:
        if len(endpoint.events) >= count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"endpoint {endpoint.device_id} only received {len(endpoint.events)} events")


async def _wait_for_command(endpoint: RecordingEndpoint, count: int = 1) -> Event:
    """等待端侧收到第 N 个 command.requested 事件。"""

    deadline = asyncio.get_running_loop().time() + 1
    while asyncio.get_running_loop().time() < deadline:
        events = _command_events(endpoint)
        if len(events) >= count:
            return events[count - 1]
        await asyncio.sleep(0.01)
    raise AssertionError(f"endpoint {endpoint.device_id} did not receive command {count}")


def _command_events(endpoint: RecordingEndpoint) -> list[Event]:
    """返回端侧收到的 command.requested 事件。"""

    return [event for event in endpoint.events if event.event_name == "command.requested"]


async def _drive_successful_peer_video(app: AudioChatApp, phone: RecordingEndpoint, glass: RecordingEndpoint, result: dict) -> None:
    """驱动一次成功的 peer video 命令回执。"""

    phone_command = await _wait_for_command(phone)
    phone_cmd = phone_command.payload["command_id"]
    app.publish_control_event(
        Event(
            event_name="command.progress",
            user_id="user-peer",
            producer_id="dev-phone",
            session_id="dev-phone",
            payload={
                "command_id": phone_cmd,
                "command": "peer.video.receiver.start",
                "status": "peer.receiver.ready",
                "data": {"receiver": {"transport": "websocket", "url": "ws://127.0.0.1:19081/peer-video/task"}},
            },
        )
    )
    glass_command = await _wait_for_command(glass)
    glass_cmd = glass_command.payload["command_id"]
    app.publish_control_event(
        Event(
            event_name="command.progress",
            user_id="user-peer",
            producer_id="dev-glass",
            session_id="dev-glass",
            payload={"command_id": glass_cmd, "command": "peer.video.sender.start", "status": "peer.sender.connected"},
        )
    )
    app.publish_control_event(
        Event(
            event_name="command.completed",
            user_id="user-peer",
            producer_id="dev-phone",
            session_id="dev-phone",
            payload={"command_id": phone_cmd, "command": "peer.video.receiver.start", "result": result},
        )
    )
