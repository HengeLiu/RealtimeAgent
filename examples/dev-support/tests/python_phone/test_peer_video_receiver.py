from __future__ import annotations

import asyncio
import socket

from aiohttp import ClientSession

from audio_chat.protocol import Event
from audio_chat_python_phone_mock.peer_video import PeerVideoReceiver
from audio_chat_python_phone_mock.remote_task import RemoteCommand, RemoteTaskReporter


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
