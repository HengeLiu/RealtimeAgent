"""眼镜端运行时应用实现。"""

from dataclasses import dataclass
from typing import Any, Dict

from nextgen.apps.glass.execution.device_control import GlassDeviceControl
from nextgen.apps.glass.execution.executor_bus import GlassExecutorBus
from nextgen.apps.glass.gateway.glass_gateway import GlassGateway
from nextgen.apps.glass.sensors.event_detector import GlassEventDetector
from nextgen.apps.glass.sensors.sensor_hub import GlassSensorHub
from nextgen.shared.enums.common import CapabilityType, ExecutionType, LinkStatus, TaskPriority
from nextgen.shared.models.control import NodeEndpoint
from nextgen.shared.models.execution import ExecutionRequest
from nextgen.shared.utils.http import post_json
from nextgen.shared.utils.ws_rpc import WebSocketRpcClient, wait_for_ws_ready

@dataclass
class GlassRuntimeApp:
    """眼镜端运行时应用。

    主要功能：
    - 组合眼镜端接入层、感知模块和执行模块。

    主要属性：
    - name：运行时名称
    """

    name: str = "glass-runtime"
    device_id: str = "glass-001"

    def start(self) -> None:
        """启动眼镜端运行时。

        主要逻辑：
        - 当前阶段完成最小模块装配，便于后续扩展真实启动逻辑。
        """

        self.gateway = GlassGateway()
        self.gateway.device_id = self.device_id
        self.sensor_hub = GlassSensorHub()
        self.event_detector = GlassEventDetector(device_id=self.device_id)
        self.executor_bus = GlassExecutorBus()
        self.device_control = GlassDeviceControl()
        self.server_base_url: str | None = None
        self.peer_ws_clients: Dict[str, WebSocketRpcClient] = {}
        self.gateway.connect()

    def configure_control_endpoint(self, host: str, port: int, scheme: str = "http", base_path: str = "/device-api") -> None:
        """配置眼镜控制面地址。"""

        self.gateway.update_control_endpoint(NodeEndpoint(host=host, port=port, scheme=scheme, base_path=base_path))

    def build_registration_payload(self) -> dict:
        """构造眼镜注册载荷。"""

        registration = self.gateway.build_registration(
            display_name="眼镜",
            capabilities=[
                CapabilityType.RGB_CAMERA,
                CapabilityType.IMU,
                CapabilityType.MICROPHONE,
                CapabilityType.SPEAKER,
                CapabilityType.VIBRATOR,
            ],
        )
        return registration.to_dict()

    def build_heartbeat_payload(self, status: str = "ready") -> dict:
        """构造眼镜心跳载荷。"""

        return self.gateway.build_heartbeat(status=status).to_dict()

    def configure_server_base_url(self, server_base_url: str) -> None:
        """配置服务器控制面地址。"""

        self.server_base_url = server_base_url.rstrip("/")

    def handle_connect_peer_command(self, task_session_id: str, peer_device_id: str, peer_endpoint: dict, stream_type: str) -> dict:
        """处理服务器下发的连接手机命令。"""

        endpoint = NodeEndpoint(**peer_endpoint)
        ws_url = f"{endpoint.scheme}://{endpoint.host}:{endpoint.port}{endpoint.base_path}"
        existing_client = self.peer_ws_clients.pop(task_session_id, None)
        if existing_client is not None:
            existing_client.close()
        client = WebSocketRpcClient(ws_url)
        session = self.gateway.connect_peer_link(
            task_session_id=task_session_id,
            peer_device_id=peer_device_id,
            peer_endpoint=endpoint,
            stream_type=stream_type,
        )
        try:
            wait_for_ws_ready(client, timeout_sec=5.0)
            self.peer_ws_clients[task_session_id] = client
        except Exception as exc:
            client.close()
            self.gateway.report_broken_peer_link(task_session_id=task_session_id, reason=str(exc))
            raise
        return {
            "task_session_id": task_session_id,
            "runtime": "glass",
            "status": LinkStatus.CONNECTED.value,
            "peer_session": session,
        }

    def handle_stop_peer_link(self, task_session_id: str) -> dict:
        """处理停止任务级连接命令。"""

        client = self.peer_ws_clients.pop(task_session_id, None)
        if client is not None:
            client.close()
        self.gateway.close_peer_session(task_session_id)
        return {
            "task_session_id": task_session_id,
            "runtime": "glass",
            "status": LinkStatus.CLOSED.value,
        }

    def build_broken_link_payload(self, task_session_id: str, reason: str) -> dict:
        """构造连接异常上报载荷。"""

        self.gateway.report_broken_peer_link(task_session_id=task_session_id, reason=reason)
        return {
            "task_session_id": task_session_id,
            "runtime": "glass",
            "status": LinkStatus.BROKEN.value,
            "reason": reason,
        }

    def handle_send_find_object_frame(
        self,
        task_session_id: str,
        target_name: str,
        analysis: Dict[str, Any],
        mark_completed: bool = False,
    ) -> dict:
        """通过任务级 WebSocket 向手机发送找物单帧分析输入。"""

        client = self.peer_ws_clients.get(task_session_id)
        if client is None:
            raise RuntimeError(f"任务级连接尚未建立: {task_session_id}")
        try:
            response = client.request(
                "/find-object/frame-analysis",
                {
                    "task_session_id": task_session_id,
                    "target_name": target_name,
                    "analysis": analysis,
                    "mark_completed": mark_completed,
                },
            )
        except Exception as exc:
            self.gateway.report_broken_peer_link(task_session_id=task_session_id, reason=str(exc))
            if self.server_base_url:
                post_json(
                    f"{self.server_base_url}/tasks/{task_session_id}/peer-link/broken",
                    {"runtime": "glass", "reason": str(exc), "auto_recover": True},
                )
            raise

        hint = response["hint"]
        execution_feedback = self.executor_bus.submit(
            ExecutionRequest(
                execution_id=f"exec_{task_session_id}",
                session_id=task_session_id,
                execution_type=ExecutionType.SPEECH,
                priority=TaskPriority.HIGH,
                payload={"text": hint["text"]},
            )
        )
        execution_payload = {
            "runtime": "glass",
            "hint_text": hint["text"],
            "execution_feedback": execution_feedback.to_dict(),
            "state_summary": response.get("state_summary", {}),
        }
        if self.server_base_url:
            post_json(
                f"{self.server_base_url}/tasks/{task_session_id}/guidance-executed",
                execution_payload,
            )

        return {
            "task_session_id": task_session_id,
            "hint": hint,
            "execution_feedback": execution_feedback.to_dict(),
            "state_summary": response.get("state_summary", {}),
            "status": response.get("status"),
            "phase": response.get("phase"),
        }

    def build_voice_event(self, text: str, audio_ref: str, confidence: float):
        """基于当前事件感知模块构造语音事件。"""

        if not self.event_detector.should_emit_voice_event(text, confidence):
            return None
        return self.event_detector.build_voice_event(text, audio_ref, confidence)

    def stop(self) -> None:
        """停止眼镜端运行时。

        主要逻辑：
        - 当前阶段只保留停止入口，占位后续资源释放逻辑。
        """

        for client in self.peer_ws_clients.values():
            client.close()
        self.peer_ws_clients.clear()
        self.gateway.disconnect()
