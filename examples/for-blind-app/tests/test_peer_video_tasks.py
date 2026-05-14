from __future__ import annotations

import asyncio
import importlib
import json
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


class EagerPeerEndpoint(RecordingEndpoint):
    """会立即回报 peer video 状态的测试端侧。

    主要功能：模拟真实端侧在收到 `command.requested` 后立刻返回 progress，
    用于暴露 server 在创建订阅前下发命令导致早到回执丢失的竞态。
    """

    def __init__(self, *, app: AudioChatApp, user_id: str, device_id: str, role: str) -> None:
        super().__init__(user_id=user_id, device_id=device_id)
        self.app = app
        self.role = role

    def push_event(self, event: Event) -> None:
        """记录命令并立即回报 ready/connected。"""

        super().push_event(event)
        if event.event_name != "command.requested":
            return
        payload = dict(event.payload or {})
        command_id = str(payload.get("command_id") or "")
        command = str(payload.get("command") or "")
        if self.role == "phone" and command == "peer.video.receiver.start":
            self.app.publish_control_event(
                Event(
                    event_name="command.progress",
                    user_id=self.user_id,
                    producer_id=self.device_id,
                    session_id=self.device_id,
                    payload={
                        "command_id": command_id,
                        "command": command,
                        "status": "peer.receiver.ready",
                        "data": {"receiver": {"transport": "websocket", "url": "ws://127.0.0.1:19081/peer-video/task"}},
                    },
                )
            )
        if self.role == "glass" and command == "peer.video.sender.start":
            self.app.publish_control_event(
                Event(
                    event_name="command.progress",
                    user_id=self.user_id,
                    producer_id=self.device_id,
                    session_id=self.device_id,
                    payload={"command_id": command_id, "command": command, "status": "peer.sender.connected"},
                )
            )
        if command.endswith(".stop"):
            self.app.publish_control_event(
                Event(
                    event_name="command.completed",
                    user_id=self.user_id,
                    producer_id=self.device_id,
                    session_id=self.device_id,
                    payload={"command_id": command_id, "command": command, "result": {"stopped": True}},
                )
            )


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
        await _complete_stop_command(app, glass)
        started = await asyncio.wait_for(create_task, timeout=2)
        ref = await _wait_for_task_state(app, started.task_id, "finished")
        assert ref.state == "finished"
        assert ref.summary == "找物完成"
        task_signals_path = tmp_path / "runs/user-peer/dev-glass/task-signals.jsonl"
        task_signals = [json.loads(line) for line in task_signals_path.read_text(encoding="utf-8").splitlines()]
        started_signal = next(item for item in task_signals if item["signal_name"] == "find_object.started")
        finished_signal = next(item for item in task_signals if item["signal_name"] == "find_object.found")
        assert started_signal["allow_direct_notify"] is True
        assert started_signal["payload"]["text"] == "我开始帮你找水杯。"
        assert finished_signal["allow_direct_notify"] is True
        assert finished_signal["payload"]["message"] == "已找到水杯，位于前方"

    asyncio.run(run())


def test_peer_video_task_input_schema_hides_deprecated_mock_fields() -> None:
    """测试目标：验证 peer video Task 的模型可见输入不再暴露废弃 mock 字段。

    测试方法：读取找物和红绿灯 Task 输入的 JSON schema，检查属性列表。
    预期结果：schema 只包含真实启动参数，避免模型把 mock 字段误解成真实结果。
    """

    sys.path = [path for path in sys.path if path != str(APP_ROOT)]
    sys.path.insert(0, str(APP_ROOT))
    for name in list(sys.modules):
        if name == "capabilities" or name.startswith("capabilities."):
            sys.modules.pop(name, None)
    tasks_module = importlib.import_module("capabilities.tasks")
    properties = tasks_module.FindObjectTaskInput.model_json_schema().get("properties", {})
    assert "object_name" in properties
    assert "timeout_seconds" in properties
    assert "mock_found" not in properties
    assert "mock_confidence" not in properties
    traffic_properties = tasks_module.TrafficLightTaskInput.model_json_schema().get("properties", {})
    assert "timeout_seconds" in traffic_properties
    assert "mock_state" not in traffic_properties


def test_peer_video_task_keeps_eager_command_progress(tmp_path: Path) -> None:
    """测试目标：验证端侧快速回报 ready 时 Task 不会丢失命令状态。

    测试方法：phone 端在收到 `peer.video.receiver.start` 的同一调用栈内立即发布
    `peer.receiver.ready`，观察 glass 命令是否能马上下发。
    预期结果：server 先初始化命令回执缓存，早到 progress 被 Task 消费，glass
    命令在短时间内出现。
    """

    async def run() -> None:
        app = _app_with_peer_tasks(tmp_path)
        phone = EagerPeerEndpoint(app=app, user_id="user-peer", device_id="dev-phone", role="phone")
        glass = EagerPeerEndpoint(app=app, user_id="user-peer", device_id="dev-glass", role="glass")
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
        glass_command = await asyncio.wait_for(_wait_for_command(glass), timeout=0.3)
        assert glass_command.payload["command"] == "peer.video.sender.start"
        phone_command = _command_events(phone)[0]
        app.publish_control_event(
            Event(
                event_name="command.completed",
                user_id="user-peer",
                producer_id="dev-phone",
                session_id="dev-phone",
                payload={
                    "command_id": phone_command.payload["command_id"],
                    "command": "peer.video.receiver.start",
                    "result": {"type": "find_object", "object_name": "水杯", "found": True, "message": "已找到水杯", "source": "mock"},
                },
            )
        )
        started = await asyncio.wait_for(create_task, timeout=2)
        ref = await _wait_for_task_state(app, started.task_id, "finished")
        assert ref.state == "finished"

    asyncio.run(run())


def test_peer_video_task_waits_for_phone_vision_ready_before_starting_glass(tmp_path: Path) -> None:
    """测试目标：验证 phone 视觉模型未就绪时，Task 不会提前启动眼镜采集。

    测试方法：phone 先回报 `peer.receiver.waiting_vision`，确认 glass 没收到 sender
    命令；再回报 `peer.receiver.ready`，确认 glass sender 才下发。
    预期结果：眼镜采集严格等待 phone 视觉模型 ready。
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
                    "status": "peer.receiver.waiting_vision",
                    "data": {"receiver": {"transport": "websocket", "url": "ws://127.0.0.1:19081/peer-video/task"}},
                },
            )
        )
        await asyncio.sleep(0.05)
        assert not _command_events(glass)
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
                payload={
                    "command_id": phone_cmd,
                    "command": "peer.video.receiver.start",
                    "result": {"type": "find_object", "object_name": "水杯", "found": True, "message": "已找到水杯", "source": "mock"},
                },
            )
        )
        await _complete_stop_command(app, glass)
        started = await asyncio.wait_for(create_task, timeout=2)
        ref = await _wait_for_task_state(app, started.task_id, "finished")
        assert ref.state == "finished"

    asyncio.run(run())


def test_peer_video_task_fails_when_phone_disconnects(tmp_path: Path) -> None:
    """测试目标：验证 phone 端控制连接断开时 Task 不会继续等待视频命令超时。

    测试方法：启动找物 Task，phone 收到 receiver start 后直接标记设备离线。
    预期结果：SDK 将 phone 上未完成的 command 转成 failed，Task 快速进入 failed。
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
                input_data={"object_name": "水杯", "timeout_seconds": 30},
            )
        )
        await _wait_for_command(phone)
        app.mark_device_connection_offline("dev-phone", reason="test_phone_disconnect")
        started = await asyncio.wait_for(create_task, timeout=1)
        ref = await _wait_for_task_state(app, started.task_id, "failed")
        assert ref.state == "failed"
        assert "device offline" in ref.metadata["error"]

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
        started = await asyncio.wait_for(create_task, timeout=2)
        ref = await _wait_for_task_state(app, started.task_id, "finished")
        assert ref.state == "finished"
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


async def _wait_for_task_state(app: AudioChatApp, task_id: str, state: str, *, timeout_seconds: float = 1.0):
    """等待 TaskEngine 查询到指定状态。"""

    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        ref = app.task_engine.query(task_id)
        if ref.state == state:
            return ref
        await asyncio.sleep(0.01)
    return app.task_engine.query(task_id)


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
    await _complete_stop_command(app, glass)


async def _complete_stop_command(app: AudioChatApp, endpoint: RecordingEndpoint) -> None:
    """等待并完成端侧 stop 命令。"""

    stop_command = await _wait_for_command(endpoint, count=2)
    app.publish_control_event(
        Event(
            event_name="command.completed",
            user_id=endpoint.user_id,
            producer_id=endpoint.device_id,
            session_id=endpoint.device_id,
            payload={"command_id": stop_command.payload["command_id"], "command": stop_command.payload["command"], "result": {"stopped": True}},
        )
    )
