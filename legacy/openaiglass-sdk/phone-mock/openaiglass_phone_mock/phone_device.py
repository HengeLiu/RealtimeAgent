"""`phone-mock` 虚拟手机设备。"""

from __future__ import annotations

import json
import importlib
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
        self._phone_runtime = None
        self._sdk_task_ids_by_server_task_id: dict[str, str] = {}

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
        if handler.task_class:
            self._handle_plugin_task_start(
                server_task_id=task_id,
                task_type=task_type,
                params={**dict(handler.params), **self._params_from_payload(payload)},
            )
        for event in handler.events:
            if event.delay_ms > 0:
                time.sleep(event.delay_ms / 1000)
            self._report_task_event(task_id=task_id, event_name=event.event_name, payload=event.payload)

    def _handle_plugin_task_start(self, *, server_task_id: str, task_type: str, params: dict[str, object]) -> None:
        """启动配置声明的 Python phone-mock 任务插件。

        主要逻辑：
        1. 首次使用时按配置动态加载 `BasePhoneTask` 和 `BasePhoneProcessor` 子类。
        2. 通过 SDK `PhoneRuntime` 启动任务，明确该机制只服务于 mock 和测试。
        3. 如果任务启动阶段产生结果，则按结果中的 `event_name` 上报服务端任务事件。

        参数：
        1. `server_task_id`：服务端下发的 SDK 任务编号。
        2. `task_type`：手机任务类型。
        3. `params`：任务启动参数。

        返回值：
        1. 无。

        异常情况：
        1. 插件加载或任务启动异常会写入事件日志，不会让 phone-mock 进程退出。
        """

        try:
            runtime = self._ensure_phone_runtime()
            snapshot = runtime.start_task(task_type=task_type, params=params)
            self._sdk_task_ids_by_server_task_id[server_task_id] = snapshot.task_id
            self._log_event(
                "phone_mock.plugin_task.started",
                {
                    "server_task_id": server_task_id,
                    "phone_task_id": snapshot.task_id,
                    "task_type": task_type,
                    "state": snapshot.state,
                    "data": snapshot.data,
                },
            )
            for result in snapshot.results:
                self._report_result_if_event(server_task_id=server_task_id, result=result)
        except Exception as exc:  # pragma: no cover - 真实插件异常只写入设备日志
            self._log_event(
                "phone_mock.plugin_task.failed",
                {
                    "server_task_id": server_task_id,
                    "task_type": task_type,
                    "error": str(exc),
                },
            )

    def _ensure_phone_runtime(self):
        """按配置懒加载 phone-mock 插件运行时。"""

        if self._phone_runtime is not None:
            return self._phone_runtime

        from openaiglasses import CapabilityRegistry, PhoneRuntime

        registry = CapabilityRegistry()
        for handler in self.config.task_handlers.values():
            if handler.task_class:
                registry.register_phone_task(self._instantiate_plugin(handler.task_class))
        for plugin in self.config.processor_plugins.values():
            registry.register_phone_processor(self._instantiate_plugin(plugin.processor_class))
        self._phone_runtime = PhoneRuntime(registry=registry)
        self._log_event(
            "phone_mock.plugins.loaded",
            {
                "task_types": registry.list_phone_task_types(),
                "processor_types": registry.list_phone_processor_types(),
            },
        )
        return self._phone_runtime

    @staticmethod
    def _instantiate_plugin(import_path: str):
        """按 `module:ClassName` 或 `module.ClassName` 实例化插件。"""

        module_name, _, class_name = import_path.replace(":", ".").rpartition(".")
        if not module_name or not class_name:
            raise ValueError(f"插件路径必须是 module:ClassName 或 module.ClassName: {import_path}")
        module = importlib.import_module(module_name)
        cls = getattr(module, class_name)
        return cls()

    @staticmethod
    def _params_from_payload(payload: dict[str, object]) -> dict[str, object]:
        """从手机任务启动控制消息中提取业务参数。"""

        params = payload.get("params")
        return dict(params) if isinstance(params, dict) else {}

    def _report_result_if_event(self, *, server_task_id: str, result: dict[str, object]) -> None:
        """把插件结果中的 `event_name` 转换为服务端任务事件。"""

        event_name = str(result.get("event_name") or "").strip()
        if not event_name:
            return
        payload = {key: value for key, value in result.items() if key != "event_name"}
        self._report_task_event(task_id=server_task_id, event_name=event_name, payload=payload)

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
