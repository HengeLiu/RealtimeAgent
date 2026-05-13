from __future__ import annotations

import asyncio
import inspect
import logging
import socket
from dataclasses import dataclass, field
from typing import Any, Callable

from aiohttp import web

from .remote_task import RemoteCommand, RemoteTaskReporter
from .vision_mock import build_mock_result, fork_yolo_mock

logger = logging.getLogger(__name__)


@dataclass
class PeerVideoReceiver:
    """Python phone peer 视频接收器。

    主要功能：为一次 peer video session 打开 WebSocket 接收端，缓存最新 JPEG 帧，
    逐帧调用 YOLO mock，并在超时、停止或用户关闭时生成 command.completed。
    主要属性：`latest_frame` 保存最近帧字节，`frame_count` 记录处理帧数。
    """

    command: RemoteCommand
    reporter: RemoteTaskReporter
    listen_host: str = "0.0.0.0"
    listen_port: int = 19081
    timeout_seconds: float = 30.0
    public_host: str = "127.0.0.1"
    latest_frame: bytes | None = None
    frame_count: int = 0
    last_detection: dict[str, Any] | None = None
    sender_connected: bool = False
    complete_after_frames: int = 0
    frame_callback: Callable[[bytes, dict[str, Any]], Any] | None = None
    close_reason: str = ""
    _runner: web.AppRunner | None = field(default=None, init=False)
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, init=False)

    async def run(self) -> dict[str, Any]:
        """运行一次 peer video receiver。

        主要逻辑：发送 accepted/starting，启动 WebSocket server，上报 ready，等待
        stop 或 timeout 后发送 completed；异常时发送 failed 并释放端口。
        参数：无。
        返回值：最终 mock result。
        异常情况：端口启动失败等异常会转换为 command.failed 后继续向上抛出。
        """

        try:
            await self.reporter.accepted(message="peer video receiver accepted")
            await self.reporter.progress("peer.receiver.starting", message="正在启动手机视频接收端")
            await self._start_server()
            await self.reporter.progress(
                "peer.receiver.ready",
                message="手机视频接收端已就绪",
                data={"receiver": self.receiver_info()},
            )
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=max(0.1, self.timeout_seconds))
            except TimeoutError:
                self.close_reason = "timeout"
                await self.reporter.progress("peer.video.timeout", message="peer video receiver timeout")
            result = build_mock_result(
                purpose=self.purpose,
                object_name=self.object_name,
                frame_count=self.frame_count,
                last_detection=self.last_detection,
            )
            if self.close_reason == "server_stop":
                result = {"stopped": True, "frame_count": self.frame_count, "source": "mock"}
            if self.close_reason == "sender_disconnected" and self.frame_count <= 0:
                result = {
                    "failed": True,
                    "frame_count": self.frame_count,
                    "source": "mock",
                    "message": "眼镜视频发送端已断开，未收到可处理的视频帧",
                }
            logger.info("vision.mock.result peer_session_id=%s result=%s", self.peer_session_id, result)
            if result.get("failed"):
                await self.reporter.failed(
                    message=str(result.get("message") or "peer video failed"),
                    error_code=str(result.get("error_code") or "peer_video_sender_disconnected"),
                    data=result,
                )
            else:
                await self.reporter.completed(result=result, message=str(result.get("message") or "peer video completed"))
            return result
        except Exception as exc:  # noqa: BLE001 - 参考端需要把 receiver 错误上报给 server
            await self.reporter.failed(message=str(exc), error_code="peer_video_receiver_failed")
            raise
        finally:
            await self.close("completed")

    async def stop(self, reason: str = "server_stop") -> None:
        """停止 receiver。

        参数：`reason` 为停止原因。
        返回值：无。
        异常情况：无。
        """

        self.close_reason = reason
        self._stop_event.set()

    @property
    def peer_session_id(self) -> str:
        """返回 peer video session id。"""

        return str(self.command.params.get("peer_session_id") or self.command.command_id)

    @property
    def purpose(self) -> str:
        """返回业务 purpose。"""

        return str(self.command.params.get("purpose") or "find_object")

    @property
    def object_name(self) -> str:
        """返回找物目标名称。"""

        return str(self.command.params.get("object_name") or "目标物")

    def receiver_info(self) -> dict[str, Any]:
        """返回 sender 需要连接的 receiver 描述。"""

        host = self.public_host or _local_ip()
        return {
            "transport": "websocket",
            "url": f"ws://{host}:{self.listen_port}/peer-video/{self.peer_session_id}",
            "token": f"mock-{self.peer_session_id}",
        }

    async def _start_server(self) -> None:
        app = web.Application()
        app.router.add_get(f"/peer-video/{self.peer_session_id}", self._handle_ws)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.listen_host, self.listen_port)
        await site.start()
        logger.info("peer.video.receiver.start peer_session_id=%s url=%s", self.peer_session_id, self.receiver_info()["url"])

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.sender_connected = True
        logger.info("peer.video.sender.connected peer_session_id=%s", self.peer_session_id)
        try:
            async for message in ws:
                if message.type != web.WSMsgType.BINARY:
                    continue
                frame = bytes(message.data)
                first_frame = self.frame_count == 0
                self.latest_frame = frame
                self.frame_count += 1
                await self._emit_frame(frame)
                if first_frame:
                    await self.reporter.progress(
                        "peer.video.first_frame",
                        message="收到第一帧 peer video",
                        metrics={"frame_count": self.frame_count, "frame_size": len(frame)},
                    )
                self.last_detection = await fork_yolo_mock(frame, purpose=self.purpose, object_name=self.object_name)
                await self.reporter.progress(
                    "peer.video.frame_processed",
                    message="peer video frame processed",
                    data={"detection": self.last_detection},
                    metrics={"frame_count": self.frame_count, "frame_size": len(frame)},
                )
                if self.complete_after_frames > 0 and self.frame_count >= self.complete_after_frames:
                    self.close_reason = "mock_result"
                    self._stop_event.set()
        finally:
            if not self._stop_event.is_set():
                self.close_reason = "sender_disconnected"
                await self.reporter.progress(
                    "peer.video.sender_disconnected",
                    message="眼镜视频发送端已断开",
                    metrics={"frame_count": self.frame_count},
                )
                self._stop_event.set()
        return ws

    async def _emit_frame(self, frame: bytes) -> None:
        """把 peer video 帧转交给端侧显示和调试链路。

        主要逻辑：receiver 负责直连 WebSocket 收帧；GUI、最近帧保存和统计属于 phone
        endpoint，因此通过回调把原始 JPEG 帧和轻量元数据交给上层。
        参数：`frame` 为收到的 JPEG/PNG 字节。
        返回值：无。
        异常情况：回调异常只记录日志，不中断 peer video 任务。
        """

        if self.frame_callback is None:
            return
        metadata = {
            "peer_session_id": self.peer_session_id,
            "purpose": self.purpose,
            "object_name": self.object_name,
            "frame_count": self.frame_count,
        }
        try:
            result = self.frame_callback(frame, metadata)
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001 - 显示链路失败不应中断任务控制链路
            logger.exception("peer.video.frame_callback_failed peer_session_id=%s frame_count=%s", self.peer_session_id, self.frame_count)

    async def close(self, reason: str) -> None:
        """释放 receiver WebSocket server。

        参数：`reason` 为释放原因，仅用于日志。
        返回值：无。
        异常情况：无。
        """

        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        logger.info("peer.video.receiver.closed peer_session_id=%s reason=%s", self.peer_session_id, reason)


def _local_ip() -> str:
    """返回局域网首选 IP，失败时使用 127.0.0.1。"""

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return str(sock.getsockname()[0])
    except OSError:
        return "127.0.0.1"
