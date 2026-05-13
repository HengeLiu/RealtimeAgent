from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import json
import pkgutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aiohttp import ClientSession

from audio_chat.protocol import Event, StreamChunk, StreamChunkCodec, new_id
from .gui import GuiEventBridge, PhonePreviewWindow
from .peer_video import PeerVideoReceiver
from .playback_fallback import NetworkPythonPlaybackEndpoint as FallbackNetworkPythonPlaybackEndpoint
from .remote_task import RemoteCommand, RemoteTaskReporter
from .vision import VisionConfig, build_vision_processor

try:
    from audio_chat_python_glass.playback import NetworkPythonPlaybackEndpoint
except ModuleNotFoundError as exc:
    if exc.name != "audio_chat_python_glass":
        raise
    NetworkPythonPlaybackEndpoint = FallbackNetworkPythonPlaybackEndpoint


def _resolve_output_path(raw_path: str | Path) -> Path:
    """解析端侧输出文件路径。

    主要逻辑：绝对路径直接使用；相对路径优先相对当前工作目录，便于从仓库根目录启动
    CLI 时把最近一帧写入 `runs/audio-chat/...`。
    参数：`raw_path` 为配置路径。
    返回值：解析后的绝对路径。
    异常情况：无。
    """

    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    return (Path.cwd() / path).resolve()


def _payload_bytes(value: Any, *, default: bytes) -> bytes:
    """把配置中的帧数据归一成 bytes。

    主要逻辑：支持 bytes、普通字符串、`hex:` 字符串和本地文件路径，便于
    phone mock 在无真实相机时按固定脚本上传 RGB 帧。
    参数：`value` 是 YAML/JSON 配置值，`default` 是空配置时使用的帧内容。
    返回值：可通过 stream WebSocket 发送的 bytes。
    异常情况：文件读取失败时由 Path.read_bytes 抛出。
    """

    if value is None:
        return default
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        if value.startswith("hex:"):
            return bytes.fromhex(value[4:])
        path = Path(value)
        if path.exists():
            return path.read_bytes()
        return value.encode("utf-8")
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")


@dataclass(frozen=True)
class PhoneTaskResult:
    """phone mock 视觉任务处理结果。

    主要功能：统一描述 Python phone mock handler 的输出。
    主要属性：`status` 表示任务状态，`data` 是业务结果，`message` 用于日志和回执。
    """

    status: str
    data: dict[str, Any]
    message: str = ""


class PhoneTaskHandler:
    """Python phone mock 端侧任务处理器基类。

    主要功能：把 `command.requested` 中的 task_type 映射到本地
    视觉处理逻辑。它只代表参考端侧行为，不是 server SDK 业务 API。
    """

    task_type = ""

    async def handle(self, endpoint: "NetworkPythonPhoneMockEndpoint", command: Event, frames: list[bytes]) -> PhoneTaskResult:
        """处理端侧任务。

        参数：`endpoint` 是当前 phone mock，`command` 是 server 下发事件，
        `frames` 是本次任务可用 RGB 帧。
        返回值：`PhoneTaskResult`。
        异常情况：子类可抛出异常，phone mock 会转成 `command.failed`。
        """

        raise NotImplementedError


class PhoneTaskHandlerRegistry:
    """phone mock 任务 handler 注册表。

    主要功能：支持从配置包自动发现自定义 handler；SDK 不内置具体业务视觉任务。
    """

    def __init__(self) -> None:
        self._handlers: dict[str, PhoneTaskHandler] = {}

    def register(self, handler: PhoneTaskHandler) -> None:
        """注册 handler 实例。"""

        task_type = str(getattr(handler, "task_type", "") or "").strip()
        if not task_type:
            raise ValueError("phone task handler task_type is required")
        if task_type in self._handlers:
            raise ValueError(f"duplicate phone task handler: {task_type}")
        self._handlers[task_type] = handler

    def get(self, task_type: str) -> PhoneTaskHandler | None:
        """按 task_type 返回 handler。"""

        return self._handlers.get(task_type)

    def list_task_types(self) -> list[str]:
        """列出已注册端侧任务类型。"""

        return sorted(self._handlers)

    @classmethod
    def with_builtins(cls, packages: list[str] | None = None) -> "PhoneTaskHandlerRegistry":
        """创建带可选自动发现结果的注册表。"""

        registry = cls()
        for package in packages or []:
            registry.discover(package)
        return registry

    def discover(self, package: str) -> None:
        """从包中自动发现 PhoneTaskHandler 子类。"""

        root = importlib.import_module(package)
        modules = [root]
        package_paths = getattr(root, "__path__", None)
        if package_paths is not None:
            modules.extend(importlib.import_module(info.name) for info in pkgutil.walk_packages(package_paths, f"{root.__name__}."))
        for module in modules:
            for _, obj in inspect.getmembers(module, inspect.isclass):
                if obj is PhoneTaskHandler or not issubclass(obj, PhoneTaskHandler):
                    continue
                self.register(obj())


@dataclass(frozen=True)
class DecodedVideoFrame:
    """Python 手机端解码后的 RGB 视频帧。

    主要功能：把协议 chunk 的元数据和 OpenCV 图像对象放在一起传递。
    主要属性：`image` 是 OpenCV BGR 图像，`stream_id` / `seq` 用于日志和验收。
    """

    stream_id: str
    stream_type: str
    seq: int
    codec: str
    image: Any
    received_at: float = field(default_factory=time.time)

    @property
    def width(self) -> int:
        """返回图像宽度。"""

        return int(self.image.shape[1])

    @property
    def height(self) -> int:
        """返回图像高度。"""

        return int(self.image.shape[0])


class StreamChunkImageDecoder:
    """把 `sensor.rgb` stream chunk 解码成 OpenCV 图像。

    主要功能：支持 JPEG / PNG 图像帧，供 Python 手机端显示和后续视觉算法使用。
    主要方法：`decode()`。
    异常情况：缺少 OpenCV、codec 不支持或图像数据无法解码时抛出 RuntimeError。
    """

    def __init__(self) -> None:
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
        except Exception as exc:  # noqa: BLE001 - 需要给端侧开发者明确安装提示
            raise RuntimeError(
                "Python 手机视频显示需要安装 OpenCV：uv add opencv-python，"
                "或执行 uv sync 以安装项目依赖。"
            ) from exc
        self._cv2 = cv2
        self._np = np

    def decode(self, chunk: StreamChunk) -> DecodedVideoFrame:
        """解码单个 `sensor.rgb` chunk。

        参数：`chunk` 为 server 转发过来的 stream 数据。
        返回值：`DecodedVideoFrame`。
        异常情况：非 RGB stream、codec 不支持或解码失败时抛出 RuntimeError。
        """

        if chunk.stream_type != "sensor.rgb":
            raise RuntimeError(f"unsupported preview stream_type: {chunk.stream_type}")
        codec = (chunk.codec or "jpeg").lower()
        if codec not in {"jpeg", "jpg", "png"}:
            raise RuntimeError(f"unsupported preview codec: {chunk.codec}")
        data = self._np.frombuffer(chunk.payload, dtype=self._np.uint8)
        image = self._cv2.imdecode(data, self._cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError(f"failed to decode {codec} frame: stream_id={chunk.stream_id} seq={chunk.seq}")
        return DecodedVideoFrame(
            stream_id=chunk.stream_id,
            stream_type=chunk.stream_type,
            seq=chunk.seq,
            codec=codec,
            image=image,
        )


class FrameStore:
    """Python 手机端最近帧缓存。

    主要功能：保存最近一帧到内存，并可选写到文件，方便开发者核对实际收到的画面。
    主要方法：`update()`、`summary()`。
    """

    def __init__(self, *, save_latest_frame: str | None = None) -> None:
        self.save_latest_frame = _resolve_output_path(save_latest_frame) if save_latest_frame else None
        self.latest_frame: DecodedVideoFrame | None = None
        self.frame_count = 0
        self.first_frame_at: float | None = None
        self.last_frame_at: float | None = None

    def update(self, frame: DecodedVideoFrame, *, cv2_module: Any) -> None:
        """保存最近一帧。

        参数：`frame` 为已解码帧，`cv2_module` 为 OpenCV 模块。
        返回值：无。
        异常情况：写文件失败时由 OpenCV 或文件系统抛出异常。
        """

        now = time.time()
        self.latest_frame = frame
        self.frame_count += 1
        self.first_frame_at = self.first_frame_at or now
        self.last_frame_at = now
        if self.save_latest_frame is not None:
            self.save_latest_frame.parent.mkdir(parents=True, exist_ok=True)
            ok = cv2_module.imwrite(str(self.save_latest_frame), frame.image)
            if not ok:
                raise RuntimeError(f"failed to save latest frame: {self.save_latest_frame}")

    def summary(self) -> dict[str, Any]:
        """返回帧缓存摘要。"""

        frame = self.latest_frame
        return {
            "frame_count": self.frame_count,
            "latest_frame_path": str(self.save_latest_frame) if self.save_latest_frame else "",
            "first_frame_at": self.first_frame_at,
            "last_frame_at": self.last_frame_at,
            "latest": None
            if frame is None
            else {
                "stream_id": frame.stream_id,
                "stream_type": frame.stream_type,
                "seq": frame.seq,
                "codec": frame.codec,
                "width": frame.width,
                "height": frame.height,
            },
        }


class OpenCvVideoPreview:
    """OpenCV 图形化视频窗口。

    主要功能：用最轻量的方式把眼镜端 `sensor.rgb` 帧显示到本地窗口。
    主要方法：`show()` 刷新一帧，`close()` 释放窗口。
    """

    def __init__(self, *, cv2_module: Any, enabled: bool = True, window_title: str = "audio-chat python phone", max_fps: float = 15.0) -> None:
        self._cv2 = cv2_module
        self.enabled = enabled
        self.window_title = window_title
        self.max_fps = max(1.0, float(max_fps or 15.0))
        self._last_show_at = 0.0
        self.closed_by_user = False
        if self.enabled:
            self._cv2.namedWindow(self.window_title, self._cv2.WINDOW_NORMAL)
            self._show_placeholder()

    def _show_placeholder(self) -> None:
        """显示启动占位画面。

        主要逻辑：OpenCV 在部分平台上只调用 `namedWindow()` 不一定会弹出可见窗口；
        启动时先显示一张黑色占位图，让用户确认 Python 手机端 GUI 已启动。真正收到
        `sensor.rgb` 后会被实时画面覆盖。
        参数：无。
        返回值：无。
        异常情况：GUI 环境不可用时由 OpenCV 抛出异常。
        """

        import numpy as np  # type: ignore

        placeholder = np.zeros((360, 640, 3), dtype=np.uint8)
        self._cv2.putText(
            placeholder,
            "Waiting for sensor.rgb stream...",
            (32, 180),
            self._cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (220, 220, 220),
            2,
            self._cv2.LINE_AA,
        )
        self._cv2.imshow(self.window_title, placeholder)
        self._cv2.waitKey(1)

    def show(self, frame: DecodedVideoFrame) -> None:
        """显示一帧图像。

        参数：`frame` 为已解码帧。
        返回值：无。
        异常情况：GUI 环境不可用时 OpenCV 会抛出异常。
        """

        if not self.enabled or self.closed_by_user:
            return
        now = time.time()
        if now - self._last_show_at < 1.0 / self.max_fps:
            return
        self._last_show_at = now
        self._cv2.imshow(self.window_title, frame.image)
        key = self._cv2.waitKey(1) & 0xFF
        if key in {ord("q"), 27}:
            self.closed_by_user = True
            self.close()

    def close(self) -> None:
        """关闭视频窗口。"""

        if self.enabled:
            self._cv2.destroyWindow(self.window_title)


class NetworkPythonPhoneMockEndpoint(NetworkPythonPlaybackEndpoint):
    """基于真实网络协议的 Python 手机参考端。

    主要功能：
    1. 模拟一台同 user 下的手机端设备。
    2. 通过控制 WebSocket 注册 properties 和 supports。
    3. 通过 stream WebSocket 上传 `sensor.rgb`，并消费 `actuator.speaker`
       / `actuator.haptic` 下行 stream。

    主要方法：
    1. `run_until_registered()`：只注册设备，供多端联调测试组合使用。
    2. `run_once()`：执行一次唤醒、音频输入、输出消费闭环。

    主要属性：
    1. `sensor_events`：收到的传感器采集控制事件摘要。
    2. `actuator_streams`：收到的执行器 stream 摘要。

    异常情况：
    1. server 未启动、注册失败或 WebSocket 协议异常时向上抛出 RuntimeError。
    """

    def __init__(
        self,
        *,
        server_url: str,
        user_id: str,
        device_id: str,
        runs_root: str = "runs/audio-chat",
        auth: dict[str, Any] | None = None,
        device_name: str = "python-phone",
        client_type: str = "python-phone",
        properties: dict[str, Any] | None = None,
        supports: dict[str, Any] | None = None,
        rgb_payload: bytes | None = None,
        task_handlers: PhoneTaskHandlerRegistry | None = None,
        task_command_scripts: dict[str, list[dict[str, Any]]] | None = None,
        vision_frames: dict[str, list[bytes]] | None = None,
        display: dict[str, Any] | None = None,
        peer_video: dict[str, Any] | None = None,
        vision: dict[str, Any] | VisionConfig | None = None,
        gui_bridge: GuiEventBridge | None = None,
    ) -> None:
        display_config = dict(display or {})
        display_enabled = bool(display_config.get("enabled", False))
        display_backend = str(display_config.get("backend") or "opencv").strip().lower()
        save_latest_frame = str(display_config.get("save_latest_frame") or "") or None
        super().__init__(
            server_url=server_url,
            user_id=user_id,
            device_id=device_id,
            runs_root=runs_root,
            auth=auth,
            device_name=device_name,
            client_type=client_type,
            properties=properties or {},
            supports=supports
            or {
                "sensors": [
                    {
                        "type": "rgb",
                        "modes": ["single", "continuous"],
                        "default": {"format": "jpeg", "frequency_hz": 1, "sample_count": 1},
                    }
                ],
                "actuators": [
                    {"type": "vibrator", "commands": ["vibrate"]},
                ],
            },
            rgb_payload=rgb_payload,
        )
        self.sensor_events: list[dict[str, Any]] = []
        self.actuator_streams: list[dict[str, Any]] = []
        self.task_handlers = task_handlers or PhoneTaskHandlerRegistry.with_builtins()
        self.task_command_scripts = dict(task_command_scripts or {})
        self.vision_frames = dict(vision_frames or {})
        self.peer_video_config = dict(peer_video or {})
        self.vision_config = vision if isinstance(vision, VisionConfig) else VisionConfig.from_mapping(vision if isinstance(vision, dict) else None)
        self.vision_warmup_task: asyncio.Task | None = None
        self.peer_video_receivers: dict[str, PeerVideoReceiver] = {}
        self.peer_video_tasks: dict[str, asyncio.Task] = {}
        self.task_command_events: list[dict[str, Any]] = []
        self.frame_log: list[dict[str, Any]] = []
        self.video_frames: list[dict[str, Any]] = []
        self.video_errors: list[dict[str, Any]] = []
        self.video_decoder: StreamChunkImageDecoder | None = None
        self.frame_store: FrameStore | None = None
        self.video_preview: OpenCvVideoPreview | None = None
        self.gui_bridge = gui_bridge
        if display_enabled or save_latest_frame:
            self.video_decoder = StreamChunkImageDecoder()
            self.frame_store = FrameStore(save_latest_frame=save_latest_frame)
            if self.gui_bridge is not None:
                self.gui_bridge.emit_status(display_backend=display_backend, latest_frame_path=str(self.frame_store.save_latest_frame or ""))
                self.gui_bridge.emit_log("INFO", "Python 手机视频显示端已启动")
            if display_backend in {"opencv", "cv2"}:
                self.video_preview = OpenCvVideoPreview(
                    cv2_module=self.video_decoder._cv2,
                    enabled=display_enabled,
                    window_title=str(display_config.get("window_title") or "audio-chat python phone"),
                    max_fps=float(display_config.get("max_fps") or 15.0),
                )

    async def _open_and_send_rgb_asset(self, control_ws, stream_ws, request: Event) -> None:
        """响应 server 的 RGB 采集请求并上传一帧。

        主要逻辑：Python phone mock 默认可作为测试相机使用；收到
        `stream.control.open.requested(sensor.rgb)` 后打开输入流，发送一帧 JPEG 测试数据，
        再关闭输入流。
        参数：`control_ws` 为控制连接，`stream_ws` 为二进制数据连接，`request` 为采集请求。
        返回值：无。
        异常情况：WebSocket 发送失败时向上抛出，由控制循环任务暴露。
        """

        stream_id = new_id("stream_rgb")
        request_id = request.payload.get("request_id")
        await self._send_event(
            control_ws,
            Event(
                event_name="stream.input.opened",
                user_id=self.user_id,
                producer_id=self.device_id,
                session_id=self.device_id,
                stream_id=stream_id,
                stream_type="sensor.rgb",
                payload={
                    "stream_type": "sensor.rgb",
                    "format": {"codec": "jpeg", "sample_rate": 1, "channels": 1, "chunk_ms": 1},
                    "request_id": request_id,
                },
            ),
        )
        await stream_ws.send_bytes(
            StreamChunkCodec.encode(
                StreamChunk(
                    user_id=self.user_id,
                    session_id=self.device_id,
                    stream_id=stream_id,
                    stream_type="sensor.rgb",
                    seq=0,
                    payload=self.rgb_payload,
                    codec="jpeg",
                    sample_rate=1,
                    channels=1,
                    duration_ms=1,
                    final=True,
                    metadata={"request_id": request_id},
                )
            )
        )
        self.asset_uploads.append({"stream_id": stream_id, "payload_size": len(self.rgb_payload), "request_id": request_id})
        await asyncio.sleep(0.05)
        await self._send_event(
            control_ws,
            Event(
                event_name="stream.input.closed",
                user_id=self.user_id,
                producer_id=self.device_id,
                session_id=self.device_id,
                stream_id=stream_id,
                stream_type="sensor.rgb",
                payload={"stream_type": "sensor.rgb", "reason": "phone_mock_rgb_uploaded", "request_id": request_id},
            ),
        )

    async def _control_loop(self, control_ws, stream_ws, audio_payload: bytes | None) -> None:
        if self.gui_bridge is not None:
            self.gui_bridge.emit_status(control="open")
        async for message in control_ws:
            if message.type.name != "TEXT":
                continue
            event = Event.from_dict(json.loads(message.data))
            self.received_events.append(event)
            if self.gui_bridge is not None:
                self.gui_bridge.emit_log("DEBUG", f"control event {event.event_name} stream={event.stream_type or '-'}", debug=True)
            if event.event_name == "stream.control.open.requested":
                self.sensor_events.append(
                    {
                        "event_name": event.event_name,
                        "stream_type": event.stream_type,
                        "request_id": event.payload.get("request_id"),
                    }
                )
                if event.stream_type == "sensor.rgb":
                    await self._open_and_send_rgb_asset(control_ws, stream_ws, event)
            elif event.event_name == "command.requested":
                await self._handle_device_command(control_ws, stream_ws, event)
            elif event.event_name == "stream.output.close.requested":
                await self._send_event(
                    control_ws,
                    Event(
                        event_name="stream.output.finished",
                        user_id=self.user_id,
                        producer_id=self.device_id,
                        session_id=self.device_id,
                        stream_id=event.stream_id,
                        stream_type=event.stream_type,
                        payload={"stream_type": event.stream_type},
                    ),
                )
                await self._send_event(
                    control_ws,
                    Event(
                        event_name="stream.output.closed",
                        user_id=self.user_id,
                        producer_id=self.device_id,
                        session_id=self.device_id,
                        stream_id=event.stream_id,
                        stream_type=event.stream_type,
                        payload={"stream_type": event.stream_type, "reason": "phone_mock_closed"},
                    ),
                )
                self._output_closed.set()
            elif event.event_name == "stream.output.cancel.requested":
                await self._send_event(
                    control_ws,
                    Event(
                        event_name="stream.output.cancelled",
                        user_id=self.user_id,
                        producer_id=self.device_id,
                        session_id=self.device_id,
                        stream_id=event.stream_id,
                        stream_type=event.stream_type,
                        payload={"stream_type": event.stream_type, "reason": "phone_mock_cancelled"},
                    ),
                )
            elif event.event_name == "control.audio_session.close.requested":
                await self._send_event(
                    control_ws,
                    Event(
                        event_name="control.audio_session.closed",
                        user_id=self.user_id,
                        producer_id=self.device_id,
                        session_id=self.device_id,
                        payload={"reason": "phone_mock_closed"},
                    ),
                )
                self._session_closed.set()

    async def _handle_device_command(self, control_ws, stream_ws, event: Event) -> None:
        """处理 server 下发的 phone task 命令事件。

        主要逻辑：根据 payload.task_type 找到本地 handler，上传 RGB 帧，按协议
        上报 started / progress / completed / failed。所有媒体数据仍走 stream。
        参数：`control_ws`、`stream_ws` 为真实协议连接，`event` 为命令事件。
        返回值：无。
        异常情况：handler 异常会转换成 failed 事件。
        """

        payload = dict(event.payload or {})
        params = dict(payload.get("params") or {})
        command = str(payload.get("command") or "").strip()
        if self.gui_bridge is not None:
            self.gui_bridge.emit_log(
                "INFO",
                (
                    "收到设备命令 "
                    f"command={command or '-'} command_id={payload.get('command_id') or '-'} "
                    f"task_type={params.get('task_type') or '-'} peer_session_id={params.get('peer_session_id') or '-'}"
                ),
            )
        if command == "peer.video.receiver.start":
            await self._handle_peer_video_receiver_start(control_ws, event)
            return
        if command == "peer.video.receiver.start.stop":
            await self._handle_peer_video_receiver_stop(control_ws, event)
            return
        task_type = str(params.get("task_type") or "").strip()
        if command != "phone.task.start":
            await self._send_command_event(
                control_ws,
                event_name="command.failed",
                command=event,
                payload={
                    "task_id": str(params.get("task_id") or ""),
                    "task_type": task_type,
                    "message": f"unsupported command: {command}",
                },
            )
            return
        task_id = str(params.get("task_id") or new_id("phone_task")).strip()
        handler = self.task_handlers.get(task_type)
        if handler is None:
            await self._send_command_event(
                control_ws,
                event_name="command.failed",
                command=event,
                payload={
                    "task_id": task_id,
                    "task_type": task_type,
                    "message": f"unknown phone task handler: {task_type}",
                },
            )
            return

        try:
            await self._send_command_event(
                control_ws,
                event_name="command.accepted",
                command=event,
                payload={"task_id": task_id, "task_type": task_type, "state": "started"},
            )
            for scripted in self.task_command_scripts.get(task_type, []):
                await self._send_command_event(
                    control_ws,
                    event_name=str(scripted.get("event_name") or "command.progress"),
                    command=event,
                    payload={"task_id": task_id, "task_type": task_type, **dict(scripted.get("payload") or {})},
                )
            frames = self._frames_for_task(task_type)
            await self._upload_task_rgb_frames(control_ws, stream_ws, command=event, task_id=task_id, task_type=task_type, frames=frames)
            await self._send_command_event(
                control_ws,
                event_name="command.progress",
                command=event,
                payload={
                    "task_id": task_id,
                    "task_type": task_type,
                    "progress": 1.0,
                    "frame_count": len(frames),
                },
            )
            result = await handler.handle(self, event, frames)
            await self._send_command_event(
                control_ws,
                event_name="command.completed",
                command=event,
                payload={
                    "task_id": task_id,
                    "task_type": task_type,
                    "state": result.status,
                    "summary": result.message,
                    "result": result.data,
                },
            )
        except Exception as exc:  # noqa: BLE001 - 端侧 mock 需要把 handler 错误转为协议事件
            await self._send_command_event(
                control_ws,
                event_name="command.failed",
                command=event,
                payload={
                    "task_id": task_id,
                    "task_type": task_type,
                    "message": str(exc),
                },
            )

    def _frames_for_task(self, task_type: str) -> list[bytes]:
        frames = self.vision_frames.get(task_type) or self.vision_frames.get("*")
        if frames:
            return list(frames)
        return [self.rgb_payload]

    async def _upload_task_rgb_frames(
        self,
        control_ws,
        stream_ws,
        *,
        command: Event,
        task_id: str,
        task_type: str,
        frames: list[bytes],
    ) -> None:
        stream_id = new_id("stream_rgb")
        session_id = self.device_id
        await self._send_event(
            control_ws,
            Event(
                event_name="stream.input.opened",
                user_id=self.user_id,
                producer_id=self.device_id,
                session_id=session_id,
                stream_id=stream_id,
                stream_type="sensor.rgb",
                payload={
                    "stream_type": "sensor.rgb",
                    "format": {"codec": "jpeg", "sample_rate": 1, "channels": 1, "chunk_ms": 1},
                    "task_id": task_id,
                    "task_type": task_type,
                },
            ),
        )
        for seq, frame in enumerate(frames):
            await stream_ws.send_bytes(
                StreamChunkCodec.encode(
                    StreamChunk(
                        user_id=self.user_id,
                        session_id=session_id,
                        stream_id=stream_id,
                        stream_type="sensor.rgb",
                        seq=seq,
                        payload=frame,
                        codec="jpeg",
                        sample_rate=1,
                        channels=1,
                        duration_ms=1,
                        final=seq == len(frames) - 1,
                        metadata={"task_id": task_id, "task_type": task_type},
                    )
                )
            )
            self.frame_log.append(
                {
                    "task_id": task_id,
                    "task_type": task_type,
                    "stream_id": stream_id,
                    "seq": seq,
                    "payload_size": len(frame),
                }
            )
        await asyncio.sleep(0.05)
        await self._send_event(
            control_ws,
            Event(
                event_name="stream.input.closed",
                user_id=self.user_id,
                producer_id=self.device_id,
                session_id=session_id,
                stream_id=stream_id,
                stream_type="sensor.rgb",
                payload={"stream_type": "sensor.rgb", "reason": "phone_task_frames_uploaded", "task_id": task_id},
            ),
        )

    async def _send_command_event(self, control_ws, *, event_name: str, command: Event, payload: dict[str, Any]) -> None:
        event = Event(
            event_name=event_name,
            user_id=self.user_id,
            producer_id=self.device_id,
            session_id=self.device_id,
            payload=payload,
        )
        self.task_command_events.append({"event_name": event_name, **payload, "timestamp_ms": int(time.time() * 1000)})
        await self._send_event(control_ws, event)

    async def _handle_peer_video_receiver_start(self, control_ws, event: Event) -> None:
        """启动 peer video receiver。

        主要逻辑：解析 server 下发的 `peer.video.receiver.start`，创建
        `RemoteTaskReporter` 和 `PeerVideoReceiver`，后台运行接收端，避免阻塞控制事件
        主循环。
        参数：`control_ws` 为控制 WebSocket，`event` 为命令事件。
        返回值：无。
        异常情况：解析失败时发送 command.failed。
        """

        try:
            command = RemoteCommand.from_event(event)
            config = dict(self.peer_video_config or {})
            params = dict(command.params or {})
            yolo_mock_config = dict(config.get("yolo_mock") or {})
            timeout_seconds = float(params.get("timeout_seconds") or config.get("timeout_seconds") or 30)
            complete_after_frames = int(yolo_mock_config.get("complete_after_frames") or config.get("complete_after_frames") or 0)
            if self.gui_bridge is not None:
                self.gui_bridge.emit_log(
                    "INFO",
                    (
                        "准备启动 peer video receiver "
                        f"command_id={command.command_id} purpose={params.get('purpose') or '-'} "
                        f"object_name={params.get('object_name') or '-'} timeout={timeout_seconds}s"
                    ),
                )
            reporter = RemoteTaskReporter(
                command=command,
                producer_id=self.device_id,
                role="receiver",
                send_event=lambda item: self._send_event(control_ws, item),
            )
            receiver = PeerVideoReceiver(
                command=command,
                reporter=reporter,
                listen_host=str(config.get("listen_host") or "0.0.0.0"),
                listen_port=int(config.get("listen_port") or 19081),
                timeout_seconds=timeout_seconds,
                public_host=str(config.get("public_host") or config.get("advertise_host") or "127.0.0.1"),
                complete_after_frames=complete_after_frames,
                frame_callback=lambda frame, metadata: self._handle_peer_video_frame(frame, metadata),
                log_callback=lambda level, message: self._emit_peer_video_log(level, message),
                vision_processor=build_vision_processor(self.vision_config),
            )
            self.peer_video_receivers[command.command_id] = receiver
            task = asyncio.create_task(receiver.run())
            self.peer_video_tasks[command.command_id] = task
            task.add_done_callback(lambda finished, command_id=command.command_id: self._on_peer_video_task_done(command_id, finished))
            if self.gui_bridge is not None:
                self.gui_bridge.emit_log("INFO", f"peer video receiver 后台任务已创建 command_id={command.command_id}")
        except Exception as exc:  # noqa: BLE001 - 端侧命令解析错误需要回报给 server
            if self.gui_bridge is not None:
                self.gui_bridge.emit_log("ERROR", f"peer video receiver 启动失败: {type(exc).__name__}: {exc}")
            await self._send_command_event(
                control_ws,
                event_name="command.failed",
                command=event,
                payload={
                    "command_id": str((event.payload or {}).get("command_id") or ""),
                    "command": "peer.video.receiver.start",
                    "message": str(exc),
                },
            )

    def _start_vision_warmup(self) -> None:
        """启动 phone 端视觉模型后台预热。

        主要逻辑：phone 注册后立即加载真实 YOLO 模型和 YOLOE 文本编码依赖，提前暴露
        模型路径、依赖或下载问题。预热不阻塞设备注册和控制连接。
        返回值：无。
        异常情况：后台任务内部记录并写入 GUI。
        """

        if self.vision_config.provider == "mock":
            if self.gui_bridge is not None:
                self.gui_bridge.emit_log("INFO", "视觉模型预热跳过 provider=mock")
            return
        if self.vision_warmup_task is not None and not self.vision_warmup_task.done():
            return
        self.vision_warmup_task = asyncio.create_task(self._warmup_vision_models())

    async def _warmup_vision_models(self) -> None:
        """后台预热真实视觉模型。"""

        if self.gui_bridge is not None:
            self.gui_bridge.emit_status(vision="warming")
            self.gui_bridge.emit_log("INFO", f"视觉模型预热开始 provider={self.vision_config.provider} device={self.vision_config.device}")
        try:
            find_processor = build_vision_processor(self.vision_config)
            find_processor.log_callback = self._emit_peer_video_log
            await find_processor.prepare_session(purpose="find_object", object_name="目标物")
            traffic_processor = build_vision_processor(self.vision_config)
            traffic_processor.log_callback = self._emit_peer_video_log
            await traffic_processor.prepare_session(purpose="traffic_light", object_name="")
        except asyncio.CancelledError:
            if self.gui_bridge is not None:
                self.gui_bridge.emit_log("INFO", "视觉模型预热已取消")
            raise
        except Exception as exc:  # noqa: BLE001 - 预热失败要可观察，但不能断开 phone 控制连接
            if self.gui_bridge is not None:
                self.gui_bridge.emit_status(vision="failed", last_error=str(exc))
                self.gui_bridge.emit_log("ERROR", f"视觉模型预热失败: {type(exc).__name__}: {exc}")
            return
        if self.gui_bridge is not None:
            self.gui_bridge.emit_status(vision="ready")
            self.gui_bridge.emit_log("INFO", "视觉模型预热完成，phone 端已就绪")

    async def _cancel_vision_warmup(self) -> None:
        """取消仍在运行的视觉预热任务。"""

        task = self.vision_warmup_task
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def _on_peer_video_task_done(self, command_id: str, task: asyncio.Task) -> None:
        """收口 peer video 后台任务结果。

        主要逻辑：清理 receiver/task 映射，并主动读取 task exception，避免 asyncio 报
        `Task exception was never retrieved`。正常失败应已经通过 command.failed 回报给 server。
        参数：`command_id` 为原始 command id，`task` 为已结束的后台任务。
        返回值：无。
        异常情况：无。
        """

        self.peer_video_tasks.pop(command_id, None)
        self.peer_video_receivers.pop(command_id, None)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            if self.gui_bridge is not None:
                self.gui_bridge.emit_log("ERROR", f"peer video receiver 异常退出: {type(exc).__name__}: {exc}")
            return
        if self.gui_bridge is not None:
            self.gui_bridge.emit_log("INFO", f"peer video receiver 后台任务结束 command_id={command_id}")

    def _emit_peer_video_log(self, level: str, message: str) -> None:
        """把 peer video receiver 内部日志写入 GUI 面板。

        参数：`level` 为日志级别，`message` 为日志内容。
        返回值：无。
        异常情况：GUI 不存在时忽略。
        """

        if self.gui_bridge is not None:
            self.gui_bridge.emit_log(level, message, debug=str(level).upper() == "DEBUG")

    def _handle_peer_video_frame(self, frame: bytes, metadata: dict[str, Any]) -> None:
        """把 peer video 直连帧送入本地视频显示链路。

        主要逻辑：peer video 不经过 server stream 服务，但对 phone 端窗口来说仍然是
        `sensor.rgb` 图像帧。这里复用已有解码、最近帧保存和 GUI 刷新逻辑，避免
        phone 窗口只能看到 server 转发的 RGB，而看不到任务直连视频。
        参数：`frame` 为 peer sender 发来的 JPEG/PNG 字节，`metadata` 为 peer session 信息。
        返回值：无。
        异常情况：解码失败会由 `_handle_video_chunk()` 记录到 `video_errors`。
        """

        seq = max(0, int(metadata.get("frame_count") or 1) - 1)
        stream_id = f"peer_{metadata.get('peer_session_id') or 'video'}"
        self._handle_video_chunk(
            StreamChunk(
                user_id=self.user_id,
                session_id=self.device_id,
                stream_id=stream_id,
                stream_type="sensor.rgb",
                seq=seq,
                payload=frame,
                codec="jpeg",
                sample_rate=1,
                channels=1,
                duration_ms=1,
                final=False,
                metadata={"source": "peer_video", **metadata},
            )
        )

    async def _handle_peer_video_receiver_stop(self, control_ws, event: Event) -> None:
        """停止 peer video receiver。

        主要逻辑：根据 stop params.command_id 找到原始 receiver，通知其退出，并对 stop
        命令本身发送 completed 回执。
        参数：`control_ws` 为控制 WebSocket，`event` 为 stop 命令事件。
        返回值：无。
        异常情况：无 receiver 时仍返回 completed，保持取消幂等。
        """

        payload = dict(event.payload or {})
        params = dict(payload.get("params") or {})
        target_command_id = str(params.get("command_id") or "").strip()
        receiver = self.peer_video_receivers.get(target_command_id)
        if receiver is not None:
            await receiver.stop(str(params.get("reason") or "server_stop"))
        await self._send_command_event(
            control_ws,
            event_name="command.completed",
            command=event,
            payload={
                "command_id": str(payload.get("command_id") or ""),
                "command": "peer.video.receiver.start.stop",
                "target_command_id": target_command_id,
                "result": {"stopped": True},
            },
        )

    async def _stop_all_peer_video_receivers(self, *, reason: str) -> None:
        """停止当前 phone 端所有 peer video receiver。

        主要逻辑：CLI 退出、控制连接断开或窗口关闭时，显式通知每个 receiver 停止，
        并短暂等待后台任务释放本地 WebSocket 端口，避免下次启动仍占用端口或 server
        侧长期等待旧命令。
        参数：`reason` 为停止原因。
        返回值：无。
        异常情况：单个 receiver 停止失败只记录到 GUI，不阻塞整体退出。
        """

        receivers = list(self.peer_video_receivers.values())
        for receiver in receivers:
            try:
                await receiver.stop(reason)
            except Exception as exc:  # noqa: BLE001 - 退出清理必须尽量完成其他 receiver
                if self.gui_bridge is not None:
                    self.gui_bridge.emit_log("ERROR", f"peer video receiver 停止失败: {type(exc).__name__}: {exc}")
        tasks = [task for task in self.peer_video_tasks.values() if not task.done()]
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=2.0)
            _ = done
            for task in pending:
                task.cancel()
        self.peer_video_receivers.clear()
        self.peer_video_tasks.clear()

    async def _stream_loop(self, control_ws, stream_ws) -> None:
        if self.gui_bridge is not None:
            self.gui_bridge.emit_status(stream="open")
        async for message in stream_ws:
            if message.type.name != "BINARY":
                continue
            from audio_chat.protocol import StreamChunkCodec

            chunk = StreamChunkCodec.decode(message.data)
            if chunk.stream_type == "sensor.rgb":
                self._handle_video_chunk(chunk)
                continue
            if chunk.stream_id not in self._started_output_streams:
                self._started_output_streams.add(chunk.stream_id)
                await self._send_event(
                    control_ws,
                    Event(
                        event_name="stream.output.started",
                        user_id=self.user_id,
                        producer_id=self.device_id,
                        session_id=self.device_id,
                        stream_id=chunk.stream_id,
                        stream_type=chunk.stream_type,
                        payload={"stream_type": chunk.stream_type},
                    ),
                )
            self.output_chunks.append(chunk)
            self.actuator_streams.append(_chunk_summary(chunk))

    def _handle_video_chunk(self, chunk: StreamChunk) -> None:
        """处理 server 转发给手机端的 RGB 视频帧。

        主要逻辑：解码 JPEG/PNG，更新最近帧缓存，并在启用 GUI 时刷新 OpenCV 窗口。
        参数：`chunk` 为 `sensor.rgb` stream chunk。
        返回值：无。
        异常情况：解码失败会记录到 `video_errors`，不让单帧坏数据断开设备。
        """

        if self.video_decoder is None:
            self.video_frames.append(_chunk_summary(chunk))
            return
        try:
            frame = self.video_decoder.decode(chunk)
            if self.frame_store is not None:
                self.frame_store.update(frame, cv2_module=self.video_decoder._cv2)
            if self.video_preview is not None:
                self.video_preview.show(frame)
            if self.gui_bridge is not None:
                summary = self.gui_bridge.emit_frame(frame)
                self.gui_bridge.emit_log(
                    "INFO",
                    f"收到 sensor.rgb 帧 stream={summary.stream_id} seq={summary.seq} size={summary.width}x{summary.height}",
                )
            self.video_frames.append(
                {
                    **_chunk_summary(chunk),
                    "width": frame.width,
                    "height": frame.height,
                    "codec": frame.codec,
                }
            )
        except Exception as exc:  # noqa: BLE001 - 端侧视频预览应记录坏帧并继续收流
            if self.gui_bridge is not None:
                self.gui_bridge.emit_status(last_error=str(exc))
                self.gui_bridge.emit_log("ERROR", f"视频帧解码失败: {type(exc).__name__}: {exc}")
            self.video_errors.append(
                {
                    "stream_id": chunk.stream_id,
                    "seq": chunk.seq,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )

    async def run_forever(self) -> dict[str, Any]:
        """长驻运行 Python 手机视频显示端。

        主要逻辑：建立控制和 stream WebSocket，注册设备后持续接收 server 转发的
        `sensor.rgb` chunk，并在本地窗口刷新画面。该方法通常由 CLI 启动，直到用户
        Ctrl-C 或连接断开。
        参数：无。
        返回值：退出时的运行摘要。
        异常情况：server 未启动、注册失败或 WebSocket 异常时向上抛出。
        """

        async with ClientSession() as session:
            control_ws = await self.run_until_registered(session=session)
            if self.gui_bridge is not None:
                self.gui_bridge.emit_status(registered=True, control="open")
                self.gui_bridge.emit_log("INFO", f"设备已注册 device_id={self.device_id}")
            self._start_vision_warmup()
            try:
                async with session.ws_connect(self._stream_url()) as stream_ws:
                    control_task = asyncio.create_task(self._control_loop(control_ws, stream_ws, None))
                    stream_task = asyncio.create_task(self._stream_loop(control_ws, stream_ws))
                    try:
                        await asyncio.gather(control_task, stream_task)
                    finally:
                        control_task.cancel()
                        stream_task.cancel()
            finally:
                await self._stop_all_peer_video_receivers(reason="phone_endpoint_closing")
                await self._cancel_vision_warmup()
                if self.video_preview is not None:
                    self.video_preview.close()
                if self.gui_bridge is not None:
                    self.gui_bridge.emit_status(control="closed", stream="closed")
                await control_ws.close()
        return self._build_result()

    def _build_result(self) -> dict[str, Any]:
        result = super()._build_result()
        result["endpoint"] = "python-phone"
        result["sensor_events"] = list(self.sensor_events)
        result["actuator_streams"] = list(self.actuator_streams)
        result["properties"] = dict(self.properties)
        result["supports"] = dict(self.supports)
        result["task_handlers"] = self.task_handlers.list_task_types()
        result["task_command_events"] = list(self.task_command_events)
        result["frame_log"] = list(self.frame_log)
        result["video_frames"] = list(self.video_frames)
        result["video_errors"] = list(self.video_errors)
        result["video_frame_store"] = self.frame_store.summary() if self.frame_store else {}
        result["vision"] = {"provider": self.vision_config.provider, "device": self.vision_config.device}
        result["peer_video_receivers"] = {
            command_id: {"frame_count": receiver.frame_count, "peer_session_id": receiver.peer_session_id}
            for command_id, receiver in self.peer_video_receivers.items()
        }
        result["gui"] = self.gui_bridge.snapshot() if self.gui_bridge else {}
        return result


def _chunk_summary(chunk: StreamChunk) -> dict[str, Any]:
    """生成执行器 stream 诊断摘要。"""

    return {
        "stream_id": chunk.stream_id,
        "stream_type": chunk.stream_type,
        "seq": chunk.seq,
        "payload_size": len(chunk.payload),
        "final": chunk.final,
    }


def _build_endpoint_from_config(config: dict[str, Any], *, gui_bridge: GuiEventBridge | None = None) -> NetworkPythonPhoneMockEndpoint:
    """从配置创建 Python phone mock 端点。

    主要逻辑：集中处理 handler、视觉帧和显示配置，避免 CLI、测试和 GUI 入口各自拼装。
    参数：`config` 为 YAML/JSON 配置，`gui_bridge` 为可选 GUI 事件桥。
    返回值：配置好的 `NetworkPythonPhoneMockEndpoint`。
    异常情况：配置中的 handler 包或帧文件非法时向上抛出。
    """

    handler_registry = PhoneTaskHandlerRegistry.with_builtins(list(config.get("handler_packages") or []))
    vision_frames = _vision_frames_from_config(dict(config.get("vision_frames") or {}))
    display_config = dict(config.get("display") or {})
    return NetworkPythonPhoneMockEndpoint(
        server_url=config.get("server_url", "http://127.0.0.1:8765"),
        user_id=config.get("user_id", "user-phone-mock-001"),
        device_id=config.get("device_id", "dev-python-phone-001"),
        runs_root=config.get("runs_root", "runs/audio-chat"),
        auth=dict(config.get("auth") or {"mode": "disabled"}),
        device_name=str(config.get("name") or config.get("device_name") or "python-phone"),
        properties=dict(config.get("properties") or {}) or None,
        supports=dict(config.get("supports") or {}) or None,
        task_handlers=handler_registry,
        task_command_scripts=dict(config.get("task_command_scripts") or {}),
        vision_frames=vision_frames,
        display=display_config,
        peer_video=dict(config.get("peer_video") or {}),
        vision=dict(config.get("vision") or {}),
        gui_bridge=gui_bridge,
    )


async def run_network_phone_mock(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """按配置运行一次 Python phone mock 网络闭环。"""

    config = config or {}
    endpoint = _build_endpoint_from_config(config)
    display_config = dict(config.get("display") or {})
    mode = str(config.get("mode") or "").strip()
    if mode in {"register_only", "network_register"} or (not mode and not display_config):
        async with ClientSession() as session:
            control_ws = await endpoint.run_until_registered(session=session)
            try:
                await asyncio.sleep(float(config.get("hold_seconds", 0.05)))
            finally:
                await control_ws.close()
        result = endpoint._build_result()
        result["passed"] = "control.device.registered" in result["event_names"]
        result["mode"] = "register_only"
        return result
    if mode in {"once", "network_once"}:
        return await endpoint.run_once()
    return await endpoint.run_forever()


def run_network_phone_preview_gui(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """启动 PySide6 手机视频预览窗口。

    主要逻辑：Qt 窗口运行在主线程，网络协议循环运行在后台线程；两者通过
    `GuiEventBridge` 传递状态、日志和视频帧。
    参数：`config` 为 phone.preview.yaml 解析结果。
    返回值：窗口关闭时的运行摘要。
    异常情况：server 未启动、PySide6 未安装或配置错误时抛出明确异常。
    """

    config = config or {}
    display_config = dict(config.get("display") or {})
    bridge = GuiEventBridge(
        log_limit=int(display_config.get("log_limit") or 200),
        show_debug_events=bool(display_config.get("show_debug_events", False)),
    )
    endpoint = _build_endpoint_from_config(config, gui_bridge=bridge)
    result_holder: dict[str, Any] = {}
    error_holder: dict[str, BaseException] = {}

    def network_worker() -> None:
        try:
            result_holder.update(asyncio.run(endpoint.run_forever()))
        except BaseException as exc:  # noqa: BLE001 - 后台线程错误要显示到 GUI 并回传给 CLI
            error_holder["error"] = exc
            bridge.emit_status(last_error=str(exc), control="error", stream="error")
            bridge.emit_log("ERROR", f"网络循环退出: {type(exc).__name__}: {exc}")

    thread = threading.Thread(target=network_worker, name="python-phone-network", daemon=True)
    thread.start()
    window = PhonePreviewWindow(
        bridge=bridge,
        title=str(display_config.get("window_title") or "audio-chat Python Phone"),
    )
    exit_code = window.show()
    result = result_holder or endpoint._build_result()
    result["mode"] = "preview"
    result["gui_exit_code"] = exit_code
    if error_holder:
        result["passed"] = False
        result["error"] = str(error_holder["error"])
    return result


def _vision_frames_from_config(config: dict[str, Any]) -> dict[str, list[bytes]]:
    """读取 phone mock 视觉帧配置。"""

    result: dict[str, list[bytes]] = {}
    for task_type, value in config.items():
        frames = value if isinstance(value, list) else [value]
        result[str(task_type)] = [
            _payload_bytes(frame, default=b"\xff\xd8phone-task-frame\xff\xd9")
            for frame in frames
        ]
    return result


def main(argv: list[str] | None = None) -> None:
    """Python phone mock CLI 入口。"""

    parser = argparse.ArgumentParser(prog="python -m audio_chat_python_phone_mock", description="启动 audio-chat Python phone mock")
    parser.add_argument("--config", default="")
    args = parser.parse_args(argv)
    config: dict[str, Any] = {}
    if args.config:
        text = Path(args.config).read_text(encoding="utf-8")
        if text.strip().startswith("{"):
            config = json.loads(text)
        else:
            import yaml

            config = yaml.safe_load(text) or {}
    display_config = dict(config.get("display") or {})
    mode = str(config.get("mode") or "").strip()
    backend = str(display_config.get("backend") or "").strip().lower()
    if mode == "preview" and bool(display_config.get("enabled", False)) and backend == "pyside6":
        result = run_network_phone_preview_gui(config)
    else:
        result = asyncio.run(run_network_phone_mock(config))
    if not result.get("passed", False):
        raise SystemExit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))
