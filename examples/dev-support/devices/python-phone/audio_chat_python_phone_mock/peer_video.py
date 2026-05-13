from __future__ import annotations

import asyncio
import inspect
import logging
import socket
from dataclasses import dataclass, field
from typing import Any, Callable

from aiohttp import web

from .remote_task import RemoteCommand, RemoteTaskReporter
from .vision import VisionProcessor, build_vision_processor

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
    log_callback: Callable[[str, str], Any] | None = None
    vision_processor: VisionProcessor | None = None
    close_reason: str = ""
    failure_message: str = ""
    failure_code: str = "peer_video_failed"
    _runner: web.AppRunner | None = field(default=None, init=False)
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    _vision_prepare_task: asyncio.Task | None = field(default=None, init=False)
    _timeout_task: asyncio.Task | None = field(default=None, init=False)

    async def run(self) -> dict[str, Any]:
        """运行一次 peer video receiver。

        主要逻辑：发送 accepted/starting，启动 WebSocket server，上报 waiting_vision，
        视觉准备完成后上报 ready，然后等待 stop 或业务超时后发送 completed；异常时发送
        failed 并释放端口。
        参数：无。
        返回值：最终 mock result。
        异常情况：端口启动失败等异常会转换为 command.failed 后继续向上抛出。
        """

        try:
            self._emit_log(
                "INFO",
                (
                    "peer video receiver 收到启动请求 "
                    f"peer_session_id={self.peer_session_id} purpose={self.purpose} "
                    f"object_name={self.object_name} timeout={self.timeout_seconds}s"
                ),
            )
            await self.reporter.accepted(message="peer video receiver accepted")
            await self.reporter.progress("peer.receiver.starting", message="正在启动手机视频接收端")
            self.vision_processor = self.vision_processor or build_vision_processor(None)
            self.vision_processor.log_callback = self._emit_log
            await self._start_server()
            await self.reporter.progress(
                "peer.receiver.waiting_vision",
                message="手机视频接收端已启动，正在准备视觉模型",
                data={"receiver": self.receiver_info(), "provider": self.vision_processor.provider},
            )
            self._vision_prepare_task = asyncio.create_task(self._prepare_vision_processor())
            self._timeout_task = asyncio.create_task(self._watch_session_timeout())
            self._emit_log("INFO", f"等待眼镜端连接 peer video url={self.receiver_info()['url']}")
            await self._stop_event.wait()
            if self.close_reason == "vision_failed":
                result = {
                    "failed": True,
                    "frame_count": self.frame_count,
                    "source": self.vision_processor.provider,
                    "message": self.failure_message or "视觉识别失败",
                    "error_code": self.failure_code,
                }
            else:
                result = await self.vision_processor.build_final_result(frame_count=self.frame_count, last_detection=self.last_detection)
            if self.close_reason == "server_stop":
                result = {"stopped": True, "frame_count": self.frame_count, "source": self.vision_processor.provider}
            if self.close_reason == "sender_disconnected" and self.frame_count <= 0:
                result = {
                    "failed": True,
                    "frame_count": self.frame_count,
                    "source": self.vision_processor.provider,
                    "message": "眼镜视频发送端已断开，未收到可处理的视频帧",
                }
            logger.info("vision.session.completed peer_session_id=%s result=%s", self.peer_session_id, result)
            self._emit_log(
                "INFO" if not result.get("failed") else "ERROR",
                (
                    "peer video receiver 结束 "
                    f"reason={self.close_reason or 'completed'} frame_count={self.frame_count} "
                    f"message={result.get('message') or '-'}"
                ),
            )
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
            provider = self.vision_processor.provider if self.vision_processor is not None else "unknown"
            result = {
                "failed": True,
                "frame_count": self.frame_count,
                "source": provider,
                "message": str(exc),
                "error_code": "peer_video_receiver_failed",
            }
            logger.exception("peer.video.receiver_failed peer_session_id=%s", self.peer_session_id)
            self._emit_log("ERROR", f"peer video receiver 失败: {type(exc).__name__}: {exc}")
            try:
                await self.reporter.failed(message=str(exc), error_code="peer_video_receiver_failed", data=result)
            except Exception:  # noqa: BLE001 - 失败回报异常只能记录，避免后台 task 泄漏异常
                logger.exception("peer.video.receiver_failed_report_failed peer_session_id=%s", self.peer_session_id)
            return result
        finally:
            await self._cancel_timeout_task()
            await self._cancel_vision_prepare_task()
            await self.close("completed")

    async def stop(self, reason: str = "server_stop") -> None:
        """停止 receiver。

        参数：`reason` 为停止原因。
        返回值：无。
        异常情况：无。
        """

        self.close_reason = reason
        self._emit_log("INFO", f"peer video receiver 收到停止请求 reason={reason} frame_count={self.frame_count}")
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
        self._emit_log("INFO", f"peer video WebSocket 已监听 url={self.receiver_info()['url']}")

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.sender_connected = True
        logger.info("peer.video.sender.connected peer_session_id=%s", self.peer_session_id)
        self._emit_log("INFO", f"眼镜端已连接 peer video peer_session_id={self.peer_session_id}")
        try:
            async for message in ws:
                if message.type != web.WSMsgType.BINARY:
                    self._emit_log("DEBUG", f"忽略非二进制 peer video 消息 type={message.type}")
                    continue
                frame = bytes(message.data)
                first_frame = self.frame_count == 0
                self.latest_frame = frame
                self.frame_count += 1
                self._emit_log("DEBUG", f"收到 peer video 帧 frame_count={self.frame_count} bytes={len(frame)}")
                await self._emit_frame(frame)
                if first_frame:
                    self._emit_log("INFO", f"收到第一帧 peer video bytes={len(frame)}")
                    await self.reporter.progress(
                        "peer.video.first_frame",
                        message="收到第一帧 peer video",
                        metrics={"frame_count": self.frame_count, "frame_size": len(frame)},
                    )
                if self.vision_processor is None:
                    self.vision_processor = build_vision_processor(None)
                    self.vision_processor.log_callback = self._emit_log
                await self._wait_for_vision_ready()
                self._emit_log("DEBUG", f"开始处理 peer video 帧 frame_count={self.frame_count}")
                frame_result = await self.vision_processor.process_frame(frame, frame_count=self.frame_count)
                self.last_detection = dict(frame_result.detection)
                self._emit_log(
                    "INFO",
                    (
                        "peer video 帧处理完成 "
                        f"frame_count={self.frame_count} should_complete={frame_result.should_complete} "
                        f"metrics={dict(frame_result.metrics)} detection={self._summarize_detection(self.last_detection)}"
                    ),
                )
                await self.reporter.progress(
                    "peer.video.frame_processed",
                    message="peer video frame processed",
                    data={"detection": self.last_detection},
                    metrics={"frame_count": self.frame_count, "frame_size": len(frame), **dict(frame_result.metrics)},
                )
                if frame_result.should_complete:
                    self.close_reason = "vision_result"
                    self._emit_log("INFO", f"视觉结果触发任务完成 frame_count={self.frame_count}")
                    self._stop_event.set()
                if self.complete_after_frames > 0 and self.frame_count >= self.complete_after_frames:
                    self.close_reason = "test_frame_limit"
                    self._emit_log("INFO", f"测试帧数上限触发任务完成 frame_count={self.frame_count}")
                    self._stop_event.set()
        except Exception as exc:  # noqa: BLE001 - 视觉识别异常需要结束本次远程任务
            logger.exception("peer.video.vision_failed peer_session_id=%s", self.peer_session_id)
            self._emit_log("ERROR", f"peer video 视觉处理失败: {type(exc).__name__}: {exc}")
            self.close_reason = "vision_failed"
            self.failure_message = str(exc)
            self.failure_code = "peer_video_vision_failed"
            self._stop_event.set()
        finally:
            if not self._stop_event.is_set():
                self.close_reason = "sender_disconnected"
                self._emit_log("WARNING", f"眼镜端 peer video 已断开 frame_count={self.frame_count}")
                await self.reporter.progress(
                    "peer.video.sender_disconnected",
                    message="眼镜视频发送端已断开",
                    metrics={"frame_count": self.frame_count},
                )
                self._stop_event.set()
        return ws

    async def _prepare_vision_processor(self) -> None:
        """后台准备视觉处理器。

        主要逻辑：手机端 WebSocket 先启动并上报 waiting_vision；真实模型加载和
        YOLOE 文本编码完成后，才上报 ready 允许眼镜开始采集。准备失败时主动结束
        本次任务，让 server 收到 command.failed。
        """

        try:
            if self.vision_processor is None:
                self.vision_processor = build_vision_processor(None)
                self.vision_processor.log_callback = self._emit_log
            self._emit_log("INFO", f"视觉处理器准备开始 provider={self.vision_processor.provider} purpose={self.purpose}")
            await self.vision_processor.prepare_session(purpose=self.purpose, object_name=self.object_name)
            self._emit_log("INFO", f"视觉处理器准备完成 provider={self.vision_processor.provider} purpose={self.purpose}")
            if self._stop_event.is_set():
                return
            await self.reporter.progress(
                "peer.receiver.ready",
                message="手机视觉模型已就绪，开始接收眼镜视频",
                data={"receiver": self.receiver_info(), "provider": self.vision_processor.provider},
            )
        except asyncio.CancelledError:
            self._emit_log("INFO", "视觉处理器准备任务已取消")
            raise
        except Exception as exc:  # noqa: BLE001 - 准备失败需要转为远程任务失败
            logger.exception("peer.video.vision_prepare_failed peer_session_id=%s", self.peer_session_id)
            self._emit_log("ERROR", f"视觉处理器准备失败: {type(exc).__name__}: {exc}")
            self.close_reason = "vision_failed"
            self.failure_message = str(exc)
            self.failure_code = "peer_video_vision_prepare_failed"
            self._stop_event.set()

    async def _watch_session_timeout(self) -> None:
        """在视觉准备完成后启动业务超时计时。

        主要逻辑：首次 YOLOE 文本编码可能触发大权重下载，这属于模型预热，不应计入
        用户的找物/红绿灯识别时长。只有视觉处理器准备完成后，才开始计算
        `timeout_seconds`。
        """

        try:
            task = self._vision_prepare_task
            if task is not None:
                await task
            if self.close_reason == "vision_failed" or self._stop_event.is_set():
                return
            self._emit_log("INFO", f"peer video 业务超时计时开始 timeout={self.timeout_seconds}s")
            await asyncio.sleep(max(0.1, self.timeout_seconds))
            if self._stop_event.is_set():
                return
            self.close_reason = "timeout"
            self._emit_log("WARNING", f"peer video receiver 超时 frame_count={self.frame_count}")
            await self.reporter.progress("peer.video.timeout", message="peer video receiver timeout")
            self._stop_event.set()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 超时 watcher 失败要转为任务失败
            logger.exception("peer.video.timeout_watcher_failed peer_session_id=%s", self.peer_session_id)
            self.close_reason = "vision_failed"
            self.failure_message = str(exc)
            self.failure_code = "peer_video_timeout_watcher_failed"
            self._stop_event.set()

    async def _wait_for_vision_ready(self) -> None:
        """等待后台视觉准备任务完成。"""

        task = self._vision_prepare_task
        if task is not None and not task.done():
            self._emit_log("INFO", f"等待视觉处理器准备完成 frame_count={self.frame_count}")
            await task
        if self.close_reason == "vision_failed":
            raise RuntimeError(self.failure_message or "视觉处理器准备失败")

    async def _cancel_vision_prepare_task(self) -> None:
        """取消仍在运行的视觉准备任务。"""

        task = self._vision_prepare_task
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _cancel_timeout_task(self) -> None:
        """取消仍在运行的业务超时任务。"""

        task = self._timeout_task
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _emit_frame(self, frame: bytes) -> None:
        """把 peer video 帧转交给端侧显示和调试链路。

        主要逻辑：receiver 负责直连 WebSocket 收帧；GUI、最近帧保存和统计属于 phone
        endpoint，因此通过回调把原始 JPEG 帧和轻量元数据交给上层。
        参数：`frame` 为收到的 JPEG/PNG 字节。
        返回值：无。
        异常情况：回调异常只记录日志，不中断 peer video 任务。
        """

        if self.frame_callback is None:
            self._emit_log("DEBUG", f"peer video 帧未配置显示回调 frame_count={self.frame_count}")
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
            self._emit_log("ERROR", f"peer video 显示回调失败 frame_count={self.frame_count}")

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
        self._emit_log("INFO", f"peer video receiver 已释放 reason={reason}")

    def _emit_log(self, level: str, message: str) -> None:
        """向 Python logger 和可选 GUI 日志同时输出 receiver 状态。

        参数：`level` 为日志级别，`message` 为可读消息。
        返回值：无。
        异常情况：GUI 日志回调失败只写入 Python logger，不影响任务。
        """

        normalized = str(level or "INFO").upper()
        log_method = getattr(logger, normalized.lower(), logger.info)
        log_method(message)
        if self.log_callback is None:
            return
        try:
            result = self.log_callback(normalized, message)
            if inspect.isawaitable(result):
                asyncio.create_task(result)
        except Exception:  # noqa: BLE001 - 日志回调失败不应中断任务
            logger.exception("peer.video.gui_log_failed peer_session_id=%s", self.peer_session_id)

    @staticmethod
    def _summarize_detection(detection: dict[str, Any]) -> dict[str, Any]:
        """生成适合日志展示的检测摘要，避免 GUI 日志过长。"""

        keys = [
            "type",
            "object_name",
            "found",
            "confidence",
            "state",
            "can_cross",
            "stable",
            "direction_hint",
            "reason",
            "source",
            "message",
        ]
        return {key: detection[key] for key in keys if key in detection}


def _local_ip() -> str:
    """返回局域网首选 IP，失败时使用 127.0.0.1。"""

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return str(sock.getsockname()[0])
    except OSError:
        return "127.0.0.1"
