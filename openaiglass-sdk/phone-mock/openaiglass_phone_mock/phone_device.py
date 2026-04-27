"""`phone-mock` 虚拟手机设备。"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from urllib.request import Request, urlopen

from protocol.messages import Endpoint
from protocol.utils import create_control_message

from openaiglass_phone_mock.camera_sink import CameraSinkServer
from openaiglass_phone_mock.config import PhoneMockConfig, derive_http_base_url
from openaiglass_phone_mock.ws_client import WsClient


@dataclass(slots=True)
class PhoneMockResult:
    """`phone-mock` 运行结果。"""

    ok: bool
    received_task_count: int
    reported_event_count: int


class PhoneMockDevice:
    """按真实 phone 协议运行的独立 mock 设备。

    主要功能：
    1. 像真实 iOS phone 一样注册到服务端并维持心跳。
    2. 接收服务端下发的手机任务启动、停止命令。
    3. 根据配置上报 mock 事件，让功能开发者验证服务端 Task 行为。
    """

    def __init__(self, config: PhoneMockConfig, *, timeout_seconds: float = 30.0, max_runtime_seconds: float = 0.0) -> None:
        self.config = config
        self.timeout_seconds = timeout_seconds
        self.max_runtime_seconds = max_runtime_seconds
        self.source = Endpoint(device_id=config.device_id, device_type="phone", module="phone-mock")
        self.target = Endpoint(device_id="server-main", device_type="server", module="server-api")
        self._heartbeat_stop = threading.Event()
        self._received_task_count = 0
        self._reported_event_count = 0
        self._camera_sink: CameraSinkServer | None = None

    def run(self) -> PhoneMockResult:
        """启动虚拟手机并进入控制消息循环。"""

        self._ensure_output_dirs()
        control: WsClient | None = None
        heartbeat_thread: threading.Thread | None = None
        try:
            self._start_camera_sink()
            control = WsClient(self.config.control_ws_url, timeout_seconds=self.timeout_seconds)
            self._send_register(control)
            registered = self._wait_for_message(control, "device.registered")
            self._log_event("device.registered", registered.get("payload", {}))
            interval_ms = int((registered.get("payload") or {}).get("heartbeat_interval_ms", 0) or self.config.heartbeat_interval_seconds * 1000)
            heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                args=(control, interval_ms),
                name=f"{self.config.device_id}-heartbeat",
                daemon=True,
            )
            heartbeat_thread.start()
            self._drain_control_messages(control)
            return PhoneMockResult(
                ok=True,
                received_task_count=self._received_task_count,
                reported_event_count=self._reported_event_count,
            )
        finally:
            self._heartbeat_stop.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=2)
            if control is not None:
                control.close()
            self._stop_camera_sink()

    def _send_register(self, control: WsClient) -> None:
        payload: dict[str, object] = {
            "device_id": self.config.device_id,
            "device_type": "phone",
            "firmware_version": "phone-mock",
            "camera_sink_ws_uri": self._camera_sink_uri(),
            "auth": {
                "mode": "pair_token",
                "pair_token": self.config.pair_token,
            },
        }
        self._send_control(control, "device.register", "request", payload)
        self._log_event("device.register.sent", {"device_id": self.config.device_id})

    def _send_control(self, control: WsClient, name: str, semantic: str, payload: dict[str, object]) -> None:
        message = create_control_message(
            semantic=semantic,
            name=name,
            source=self.source,
            target=self.target,
            payload=payload,
        )
        control.send_text(json.dumps(message.to_dict(), ensure_ascii=False, separators=(",", ":")))

    def _wait_for_message(self, control: WsClient, expected_name: str) -> dict[str, object]:
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            message = json.loads(control.recv_text())
            if message.get("name") == expected_name:
                return message
            self._handle_control_message(message)
        raise TimeoutError(f"等待 {expected_name} 超时")

    def _heartbeat_loop(self, control: WsClient, interval_ms: int) -> None:
        interval = max(interval_ms / 1000, 0.5)
        while not self._heartbeat_stop.wait(interval):
            try:
                self._send_control(control, "device.heartbeat", "notify", {"device_id": self.config.device_id})
            except Exception as exc:  # pragma: no cover - 后台线程错误只记录
                self._log_event("device.heartbeat.failed", {"error": str(exc)})
                return

    def _drain_control_messages(self, control: WsClient) -> None:
        deadline = time.monotonic() + self.max_runtime_seconds if self.max_runtime_seconds > 0 else None
        while deadline is None or time.monotonic() < deadline:
            try:
                message = json.loads(control.recv_text())
            except TimeoutError:
                if deadline is None:
                    continue
                return
            self._handle_control_message(message)

    def _handle_control_message(self, message: dict[str, object]) -> None:
        name = str(message.get("name") or "")
        if name == "sdk.phone.task.start":
            self._handle_task_start(message)
            return
        if name == "sdk.phone.task.stop":
            self._log_event("sdk.phone.task.stop", self._payload(message))
            return
        self._log_event(name or "control.message", {"payload": message.get("payload")})

    def _handle_task_start(self, message: dict[str, object]) -> None:
        payload = self._payload(message)
        task_type = str(payload.get("task_type") or "").strip()
        task_id = str(payload.get("task_id") or "").strip()
        self._received_task_count += 1
        self._log_event("sdk.phone.task.start", payload)
        handler = self.config.task_handlers.get(task_type)
        if handler is None:
            self._log_event("sdk.phone.task.unhandled", {"task_id": task_id, "task_type": task_type})
            return
        for event in handler.events:
            if event.delay_ms > 0:
                time.sleep(event.delay_ms / 1000)
            self._report_task_event(task_id=task_id, event_name=event.event_name, payload=event.payload)

    def _report_task_event(self, *, task_id: str, event_name: str, payload: dict[str, object]) -> None:
        body = json.dumps(
            {
                "task_id": task_id,
                "phone_device_id": self.config.device_id,
                "event_name": event_name,
                "payload": payload,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            f"{derive_http_base_url(self.config.control_ws_url)}/api/tasks/report-event",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            response_body = response.read().decode("utf-8")
        self._reported_event_count += 1
        self._log_event(
            "sdk.phone.task.event.reported",
            {
                "task_id": task_id,
                "event_name": event_name,
                "response": json.loads(response_body) if response_body else {},
            },
        )

    @staticmethod
    def _payload(message: dict[str, object]) -> dict[str, object]:
        payload = message.get("payload")
        return dict(payload) if isinstance(payload, dict) else {}

    def _ensure_output_dirs(self) -> None:
        if self.config.outputs is not None:
            self.config.outputs.event_log.parent.mkdir(parents=True, exist_ok=True)

    def _start_camera_sink(self) -> None:
        if self.config.camera_sink_ws_uri or not self.config.camera_sink.enabled:
            return
        camera_sink = self.config.camera_sink
        if camera_sink.save_dir is None:
            return
        self._camera_sink = CameraSinkServer(
            bind_host=camera_sink.bind_host,
            port=camera_sink.port,
            public_host=camera_sink.public_host,
            path=camera_sink.path,
            save_dir=camera_sink.save_dir,
        )
        self._camera_sink.start()
        self._log_event("camera_sink.started", {"ws_uri": self._camera_sink.ws_uri})

    def _stop_camera_sink(self) -> None:
        if self._camera_sink is not None:
            self._camera_sink.stop()
            self._camera_sink = None

    def _camera_sink_uri(self) -> str:
        if self.config.camera_sink_ws_uri:
            return self.config.camera_sink_ws_uri
        if self._camera_sink is not None:
            return self._camera_sink.ws_uri
        return ""

    def _log_event(self, event_type: str, payload: object | None = None) -> None:
        outputs = self.config.outputs
        if outputs is None:
            return
        record = {"ts": int(time.time() * 1000), "type": event_type, "payload": payload or {}}
        with outputs.event_log.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
