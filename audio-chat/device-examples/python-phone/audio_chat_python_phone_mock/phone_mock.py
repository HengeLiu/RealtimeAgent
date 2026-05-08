from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import json
import pkgutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from audio_chat_python_glass.playback import NetworkPythonPlaybackEndpoint
from audio_chat.protocol import Event, StreamChunk, StreamChunkCodec, new_id


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

    主要功能：把 `control.device.command.requested` 中的 task_type 映射到本地
    视觉处理逻辑。它只代表参考端侧行为，不是 server SDK 业务 API。
    """

    task_type = ""

    async def handle(self, endpoint: "NetworkPythonPhoneMockEndpoint", command: Event, frames: list[bytes]) -> PhoneTaskResult:
        """处理端侧任务。

        参数：`endpoint` 是当前 phone mock，`command` 是 server 下发事件，
        `frames` 是本次任务可用 RGB 帧。
        返回值：`PhoneTaskResult`。
        异常情况：子类可抛出异常，phone mock 会转成 `control.device.command.failed`。
        """

        raise NotImplementedError


class FindObjectPhoneTaskHandler(PhoneTaskHandler):
    """找物视觉任务 mock handler。

    主要功能：读取命令输入中的目标名称和 RGB 帧数量，生成稳定的“找到目标”结果，
    供 find_object 迁移样板和设备级回放使用。
    """

    task_type = "find_object_phone_task"

    async def handle(self, endpoint: "NetworkPythonPhoneMockEndpoint", command: Event, frames: list[bytes]) -> PhoneTaskResult:
        payload = dict(command.payload or {})
        input_data = dict(payload.get("input") or payload.get("params") or {})
        target = str(input_data.get("target") or input_data.get("object_name") or "目标物").strip()
        return PhoneTaskResult(
            status="found",
            message=f"已在 {len(frames)} 帧画面中找到 {target}",
            data={
                "target": target,
                "found": True,
                "frame_count": len(frames),
                "source": "python-phone-mock",
                "bbox": {"x": 0.42, "y": 0.38, "width": 0.2, "height": 0.16},
            },
        )


class TrafficLightPhoneTaskHandler(PhoneTaskHandler):
    """红绿灯视觉任务 mock handler。

    主要功能：按输入或配置返回稳定灯色，验证 traffic_light 迁移样板的 phone task
    事件和 stream 链路。
    """

    task_type = "traffic_light_phone_task"

    async def handle(self, endpoint: "NetworkPythonPhoneMockEndpoint", command: Event, frames: list[bytes]) -> PhoneTaskResult:
        payload = dict(command.payload or {})
        input_data = dict(payload.get("input") or payload.get("params") or {})
        color = str(input_data.get("expected_color") or input_data.get("color") or "green").strip() or "green"
        return PhoneTaskResult(
            status="completed",
            message=f"红绿灯识别结果：{color}",
            data={
                "color": color,
                "confidence": 0.91,
                "frame_count": len(frames),
                "source": "python-phone-mock",
            },
        )


class PhoneTaskHandlerRegistry:
    """phone mock 任务 handler 注册表。

    主要功能：内置旧 SDK 迁移样板 handler，并支持从配置包自动发现自定义 handler。
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
        """创建带内置 handler 和可选自动发现结果的注册表。"""

        registry = cls()
        registry.register(FindObjectPhoneTaskHandler())
        registry.register(TrafficLightPhoneTaskHandler())
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


class NetworkPythonPhoneMockEndpoint(NetworkPythonPlaybackEndpoint):
    """基于真实网络协议的 Python 手机参考端。

    主要功能：
    1. 模拟一台同 user 下的手机端设备。
    2. 通过控制 WebSocket 注册 properties 和 subscription。
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
        device_name: str = "python-phone-mock",
        client_type: str = "python-phone-mock",
        properties: dict[str, Any] | None = None,
        subscriptions: list[dict[str, Any]] | None = None,
        rgb_payload: bytes | None = None,
        task_handlers: PhoneTaskHandlerRegistry | None = None,
        task_event_scripts: dict[str, list[dict[str, Any]]] | None = None,
        vision_frames: dict[str, list[bytes]] | None = None,
    ) -> None:
        super().__init__(
            server_url=server_url,
            user_id=user_id,
            device_id=device_id,
            runs_root=runs_root,
            auth=auth,
            device_name=device_name,
            client_type=client_type,
            properties=properties
            or {
                "phone.task.find_object_phone_task": True,
                "phone.task.traffic_light_phone_task": True,
            },
            subscriptions=subscriptions
            or [
                {"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}},
                {"event": "stream.control.*", "filter": {"stream_type": "sensor.depth"}},
                {"event": "stream.control.*", "filter": {"stream_type": "sensor.imu"}},
                {"event": "stream.output.*", "filter": {"stream_type": "actuator.speaker"}},
                {"event": "stream.output.*", "filter": {"stream_type": "actuator.haptic"}},
                {"event": "control.device.command.*"},
            ],
            rgb_payload=rgb_payload,
        )
        self.sensor_events: list[dict[str, Any]] = []
        self.actuator_streams: list[dict[str, Any]] = []
        self.task_handlers = task_handlers or PhoneTaskHandlerRegistry.with_builtins()
        self.task_event_scripts = dict(task_event_scripts or {})
        self.vision_frames = dict(vision_frames or {})
        self.task_events: list[dict[str, Any]] = []
        self.frame_log: list[dict[str, Any]] = []

    async def _control_loop(self, control_ws, stream_ws, audio_payload: bytes | None) -> None:
        async for message in control_ws:
            if message.type.name != "TEXT":
                continue
            event = Event.from_dict(json.loads(message.data))
            self.received_events.append(event)
            if event.event_name == "stream.control.configure.requested":
                self.sensor_events.append(
                    {
                        "event_name": event.event_name,
                        "stream_type": event.stream_type,
                        "request_id": event.payload.get("request_id"),
                    }
                )
                if event.stream_type == "sensor.rgb":
                    await self._open_and_send_rgb_asset(control_ws, stream_ws, event)
            elif event.event_name == "control.device.command.requested":
                await self._handle_device_command(control_ws, stream_ws, event)
            elif event.event_name == "stream.output.close.requested":
                await self._send_event(
                    control_ws,
                    Event(
                        event_name="stream.output.finished",
                        user_id=self.user_id,
                        producer_id=self.device_id,
                        session_id=event.session_id,
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
                        session_id=event.session_id,
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
                        session_id=event.session_id,
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
                        session_id=event.session_id,
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
        task_type = str(payload.get("task_type") or payload.get("command_name") or "").strip()
        task_id = str(payload.get("task_id") or new_id("phone_task")).strip()
        handler = self.task_handlers.get(task_type)
        if handler is None:
            await self._send_command_event(
                control_ws,
                event_name="control.device.command.failed",
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
                event_name="control.device.command.started",
                command=event,
                payload={"task_id": task_id, "task_type": task_type, "state": "started"},
            )
            for scripted in self.task_event_scripts.get(task_type, []):
                await self._send_command_event(
                    control_ws,
                    event_name=str(scripted.get("event_name") or "control.device.command.progress"),
                    command=event,
                    payload={"task_id": task_id, "task_type": task_type, **dict(scripted.get("payload") or {})},
                )
            frames = self._frames_for_task(task_type)
            await self._upload_task_rgb_frames(control_ws, stream_ws, command=event, task_id=task_id, task_type=task_type, frames=frames)
            await self._send_command_event(
                control_ws,
                event_name="control.device.command.progress",
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
                event_name="control.device.command.completed",
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
                event_name="control.device.command.failed",
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
        session_id = command.session_id or str(command.payload.get("session_id") or "") or self._session_id or new_id("sess")
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
            session_id=command.session_id or str(command.payload.get("session_id") or "") or None,
            payload=payload,
        )
        self.task_events.append({"event_name": event_name, **payload, "timestamp_ms": int(time.time() * 1000)})
        await self._send_event(control_ws, event)

    async def _stream_loop(self, control_ws, stream_ws) -> None:
        async for message in stream_ws:
            if message.type.name != "BINARY":
                continue
            from audio_chat.protocol import StreamChunkCodec

            chunk = StreamChunkCodec.decode(message.data)
            if chunk.stream_id not in self._started_output_streams:
                self._started_output_streams.add(chunk.stream_id)
                await self._send_event(
                    control_ws,
                    Event(
                        event_name="stream.output.started",
                        user_id=self.user_id,
                        producer_id=self.device_id,
                        session_id=chunk.session_id,
                        stream_id=chunk.stream_id,
                        stream_type=chunk.stream_type,
                        payload={"stream_type": chunk.stream_type},
                    ),
                )
            self.output_chunks.append(chunk)
            self.actuator_streams.append(_chunk_summary(chunk))

    def _build_result(self) -> dict[str, Any]:
        result = super()._build_result()
        result["endpoint"] = "python-phone-mock"
        result["sensor_events"] = list(self.sensor_events)
        result["actuator_streams"] = list(self.actuator_streams)
        result["properties"] = dict(self.properties)
        result["subscriptions"] = list(self.subscriptions)
        result["task_handlers"] = self.task_handlers.list_task_types()
        result["task_events"] = list(self.task_events)
        result["frame_log"] = list(self.frame_log)
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


async def run_network_phone_mock(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """按配置运行一次 Python phone mock 网络闭环。"""

    config = config or {}
    handler_registry = PhoneTaskHandlerRegistry.with_builtins(list(config.get("handler_packages") or []))
    vision_frames = _vision_frames_from_config(dict(config.get("vision_frames") or {}))
    endpoint = NetworkPythonPhoneMockEndpoint(
        server_url=config.get("server_url", "http://127.0.0.1:8765"),
        user_id=config.get("user_id", "user-phone-mock-001"),
        device_id=config.get("device_id", "dev-python-phone-mock-001"),
        runs_root=config.get("runs_root", "runs/audio-chat"),
        auth=dict(config.get("auth") or {"mode": "disabled"}),
        device_name=str(config.get("name") or config.get("device_name") or "python-phone-mock"),
        properties=dict(config.get("properties") or {}) or None,
        subscriptions=list(config.get("subscriptions") or []) or None,
        task_handlers=handler_registry,
        task_event_scripts=dict(config.get("task_event_scripts") or {}),
        vision_frames=vision_frames,
    )
    if config.get("mode", "register_only") in {"register_only", "network_register"}:
        from aiohttp import ClientSession

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
    return await endpoint.run_once()


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

    parser = argparse.ArgumentParser(prog="audio-chat.phone.mock", description="启动 audio-chat Python phone mock")
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
    result = asyncio.run(run_network_phone_mock(config))
    if not result.get("passed", False):
        raise SystemExit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))
