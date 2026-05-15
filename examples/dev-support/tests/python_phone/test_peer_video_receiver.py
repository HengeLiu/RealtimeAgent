from __future__ import annotations

import asyncio
import socket

from aiohttp import ClientSession

from audio_chat.protocol import Event
from audio_chat_python_phone_mock.peer_video import PeerVideoReceiver
from audio_chat_python_phone_mock.remote_task import RemoteCommand, RemoteTaskReporter
from audio_chat_python_phone_mock.vision.result import VisionFrameResult


def test_peer_video_receiver_receives_frame_and_completes_mock_result() -> None:
    """测试目标：验证 Python phone peer receiver 可独立接收 JPEG 帧并返回 mock 结果。

    测试方法：使用随机本地端口启动 receiver，配置收到 1 帧后完成，通过 aiohttp
    WebSocket 发送一帧二进制 JPEG-like 数据。
    预期结果：上报 ready、first_frame、frame_processed 和 completed，最终 result
    包含 source=mock、found=true 和 frame_count=1。
    """

    async def run() -> None:
        sent: list[Event] = []
        frames: list[dict] = []

        async def send_event(event: Event) -> None:
            sent.append(event)

        command = RemoteCommand(
            command_id="cmd-phone",
            command="peer.video.receiver.start",
            user_id="user-peer",
            session_id="dev-phone",
            params={
                "peer_session_id": "task-peer",
                "task_type": "find_object_task",
                "purpose": "find_object",
                "object_name": "水杯",
            },
        )
        receiver = PeerVideoReceiver(
            command=command,
            reporter=RemoteTaskReporter(command=command, producer_id="dev-phone", role="receiver", send_event=send_event),
            listen_host="127.0.0.1",
            listen_port=_unused_port(),
            timeout_seconds=5,
            public_host="127.0.0.1",
            complete_after_frames=1,
            frame_callback=lambda frame, metadata: frames.append({"bytes": frame, "metadata": metadata}),
        )
        task = asyncio.create_task(receiver.run())
        await _wait_for_status(sent, "peer.receiver.ready")
        async with ClientSession() as session:
            async with session.ws_connect(receiver.receiver_info()["url"]) as ws:
                await ws.send_bytes(b"\xff\xd8mock-frame\xff\xd9")
                await asyncio.sleep(0.05)
        result = await task
        assert result["source"] == "mock"
        assert result["found"] is True
        assert result["frame_count"] == 1
        assert receiver.latest_frame == b"\xff\xd8mock-frame\xff\xd9"
        assert frames == [
            {
                "bytes": b"\xff\xd8mock-frame\xff\xd9",
                "metadata": {
                    "peer_session_id": "task-peer",
                    "purpose": "find_object",
                    "object_name": "水杯",
                    "frame_count": 1,
                },
            }
        ]

        event_names = [event.event_name for event in sent]
        statuses = [event.payload.get("status") for event in sent]
        assert "command.accepted" in event_names
        assert "peer.receiver.ready" in statuses
        assert "peer.video.first_frame" in statuses
        assert "peer.video.frame_processed" in statuses
        assert sent[-1].event_name == "command.completed"

    asyncio.run(run())


def test_peer_video_receiver_emits_annotated_frame_when_yolo_returns_overlay() -> None:
    """测试目标：验证真实视觉处理返回标注图时，phone GUI 收到带识别框的预览帧。

    测试方法：注入一个返回 OpenCV 标注图的视觉处理器，发送一帧原始 peer video
    字节，记录 `frame_callback` 收到的内容。
    预期结果：回调收到的是重新编码后的 JPEG 标注图，并带有 `annotated=True`
    元数据，证明 GUI 预览链路不是只显示原始视频帧。
    """

    async def run() -> None:
        sent: list[Event] = []
        frames: list[dict] = []

        async def send_event(event: Event) -> None:
            sent.append(event)

        command = RemoteCommand(
            command_id="cmd-phone",
            command="peer.video.receiver.start",
            user_id="user-peer",
            session_id="dev-phone",
            params={
                "peer_session_id": "task-peer-yolo",
                "task_type": "find_object_task",
                "purpose": "find_object",
                "object_name": "水杯",
            },
        )
        receiver = PeerVideoReceiver(
            command=command,
            reporter=RemoteTaskReporter(command=command, producer_id="dev-phone", role="receiver", send_event=send_event),
            listen_host="127.0.0.1",
            listen_port=_unused_port(),
            timeout_seconds=5,
            public_host="127.0.0.1",
            complete_after_frames=1,
            vision_processor=AnnotatingVisionProcessor(),
            frame_callback=lambda frame, metadata: frames.append({"bytes": frame, "metadata": metadata}),
        )
        raw_frame = b"\xff\xd8raw-frame\xff\xd9"
        task = asyncio.create_task(receiver.run())
        await _wait_for_status(sent, "peer.receiver.ready")
        async with ClientSession() as session:
            async with session.ws_connect(receiver.receiver_info()["url"]) as ws:
                await ws.send_bytes(raw_frame)
                await asyncio.sleep(0.05)
        result = await task

        assert result["source"] == "yoloe"
        assert frames
        assert frames[0]["bytes"] != raw_frame
        assert frames[0]["bytes"].startswith(b"\xff\xd8")
        assert frames[0]["metadata"]["annotated"] is True
        assert frames[0]["metadata"]["display_source"] == "vision_annotated"
        assert frames[0]["metadata"]["frame_count"] == 1

    asyncio.run(run())


def test_peer_video_receiver_fails_when_sender_disconnects_before_frame() -> None:
    """测试目标：验证眼镜发送端断开且未发送任何帧时，phone receiver 不会等到超时。

    测试方法：启动 receiver，建立 WebSocket 后立即关闭，不发送二进制帧。
    预期结果：receiver 上报 `peer.video.sender_disconnected` 和 `command.failed`，
    返回结果标记 failed。
    """

    async def run() -> None:
        sent: list[Event] = []

        async def send_event(event: Event) -> None:
            sent.append(event)

        command = RemoteCommand(
            command_id="cmd-phone",
            command="peer.video.receiver.start",
            user_id="user-peer",
            session_id="dev-phone",
            params={
                "peer_session_id": "task-peer-disconnect",
                "task_type": "find_object_task",
                "purpose": "find_object",
                "object_name": "水杯",
            },
        )
        receiver = PeerVideoReceiver(
            command=command,
            reporter=RemoteTaskReporter(command=command, producer_id="dev-phone", role="receiver", send_event=send_event),
            listen_host="127.0.0.1",
            listen_port=_unused_port(),
            timeout_seconds=5,
            public_host="127.0.0.1",
        )
        task = asyncio.create_task(receiver.run())
        await _wait_for_status(sent, "peer.receiver.ready")
        async with ClientSession() as session:
            async with session.ws_connect(receiver.receiver_info()["url"]):
                pass
        result = await asyncio.wait_for(task, timeout=1)
        statuses = [event.payload.get("status") for event in sent]
        assert result["failed"] is True
        assert "peer.video.sender_disconnected" in statuses
        assert sent[-1].event_name == "command.failed"

    asyncio.run(run())


def test_peer_video_receiver_reports_vision_prepare_failure_without_leaking_task_exception() -> None:
    """测试目标：验证视觉依赖缺失时 receiver 通过 command.failed 回报且不抛后台异常。

    测试方法：注入一个 prepare_session 会失败的视觉处理器，直接等待 `run()`。
    预期结果：`run()` 返回 failed result，最后一个控制事件是 command.failed。
    """

    async def run() -> None:
        sent: list[Event] = []

        async def send_event(event: Event) -> None:
            sent.append(event)

        command = RemoteCommand(
            command_id="cmd-phone",
            command="peer.video.receiver.start",
            user_id="user-peer",
            session_id="dev-phone",
            params={"peer_session_id": "task-peer", "purpose": "find_object", "object_name": "水杯"},
        )
        receiver = PeerVideoReceiver(
            command=command,
            reporter=RemoteTaskReporter(command=command, producer_id="dev-phone", role="receiver", send_event=send_event),
            listen_host="127.0.0.1",
            listen_port=_unused_port(),
            vision_processor=FailingVisionProcessor(),
        )

        result = await receiver.run()

        assert result["failed"] is True
        assert "视觉依赖缺失" in result["message"]
        assert sent[-1].event_name == "command.failed"

    asyncio.run(run())


def test_peer_video_receiver_reports_waiting_until_slow_vision_prepare_finishes() -> None:
    """测试目标：验证慢速 YOLOE 准备期间 receiver 上报等待状态，准备完成后再 ready。

    测试方法：注入一个受控释放的视觉准备处理器，启动 receiver 后先观察 waiting，
    再释放准备任务并等待 ready。
    预期结果：模型准备完成前不会上报 `peer.receiver.ready`，避免眼镜提前采集。
    """

    async def run() -> None:
        sent: list[Event] = []
        prepare_started = asyncio.Event()
        release_prepare = asyncio.Event()

        async def send_event(event: Event) -> None:
            sent.append(event)

        command = RemoteCommand(
            command_id="cmd-phone",
            command="peer.video.receiver.start",
            user_id="user-peer",
            session_id="dev-phone",
            params={"peer_session_id": "task-peer-slow", "purpose": "find_object", "object_name": "水杯"},
        )
        receiver = PeerVideoReceiver(
            command=command,
            reporter=RemoteTaskReporter(command=command, producer_id="dev-phone", role="receiver", send_event=send_event),
            listen_host="127.0.0.1",
            listen_port=_unused_port(),
            timeout_seconds=5,
            public_host="127.0.0.1",
            vision_processor=SlowVisionProcessor(prepare_started, release_prepare),
        )

        task = asyncio.create_task(receiver.run())
        await _wait_for_status(sent, "peer.receiver.waiting_vision")
        assert not any(event.payload.get("status") == "peer.receiver.ready" for event in sent)
        assert prepare_started.is_set() is True
        assert task.done() is False

        release_prepare.set()
        await _wait_for_status(sent, "peer.receiver.ready")
        await receiver.stop("test_done")
        await asyncio.wait_for(task, timeout=1)

    asyncio.run(run())


async def _wait_for_status(events: list[Event], status: str) -> None:
    """等待 fake reporter 收到指定状态。"""

    deadline = asyncio.get_running_loop().time() + 1
    while asyncio.get_running_loop().time() < deadline:
        if any(event.payload.get("status") == status for event in events):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"status {status} not observed")


def _unused_port() -> int:
    """返回当前进程可用的临时 TCP 端口。"""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class FailingVisionProcessor:
    """用于模拟视觉依赖缺失的处理器。"""

    provider = "yolo"

    async def prepare_session(self, *, purpose: str, object_name: str) -> None:
        _ = purpose, object_name
        raise RuntimeError("视觉依赖缺失")


class SlowVisionProcessor:
    """用于模拟视觉模型准备较慢的处理器。"""

    provider = "yolo"

    def __init__(self, started: asyncio.Event, release: asyncio.Event) -> None:
        self.started = started
        self.release = release
        self.log_callback = None

    async def prepare_session(self, *, purpose: str, object_name: str) -> None:
        _ = purpose, object_name
        self.started.set()
        await self.release.wait()

    async def build_final_result(self, *, frame_count: int, last_detection: dict | None) -> dict:
        _ = last_detection
        return {"stopped": True, "frame_count": frame_count, "source": "yolo"}


class AnnotatingVisionProcessor:
    """用于模拟 YOLO 返回标注图的处理器。"""

    provider = "yolo"

    def __init__(self) -> None:
        self.log_callback = None

    async def prepare_session(self, *, purpose: str, object_name: str) -> None:
        _ = purpose, object_name

    async def process_frame(self, frame: bytes, *, frame_count: int) -> VisionFrameResult:
        _ = frame, frame_count
        import numpy as np

        image = np.zeros((32, 48, 3), dtype=np.uint8)
        image[:, :] = (0, 255, 0)
        image[4:28, 6:42] = (0, 0, 255)
        return VisionFrameResult(
            detection={"type": "find_object", "object_name": "水杯", "found": False, "source": "yoloe"},
            metrics={"provider": "yoloe", "inference_ms": 3},
            annotated_image=image,
        )

    async def build_final_result(self, *, frame_count: int, last_detection: dict | None) -> dict:
        _ = last_detection
        return {"type": "find_object", "found": False, "frame_count": frame_count, "source": "yoloe"}
