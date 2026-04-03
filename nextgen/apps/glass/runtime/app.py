"""眼镜端运行时应用实现。"""

import json
import logging
import time
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

        self.logger = logging.getLogger("nextgen.glass.runtime")
        self.gateway = GlassGateway()
        self.gateway.device_id = self.device_id
        self.sensor_hub = GlassSensorHub()
        self.event_detector = GlassEventDetector(device_id=self.device_id)
        self.device_control = GlassDeviceControl()
        self.executor_bus = GlassExecutorBus(device_control=self.device_control)
        self.server_base_url: str | None = None
        self.peer_ws_clients: Dict[str, WebSocketRpcClient] = {}
        self.test_inputs: Dict[str, Any] = {"texts": [], "images": []}
        self.last_guidance_texts: Dict[str, str] = {}
        self.last_guidance_ts: Dict[str, float] = {}
        self.gateway.connect()
        self._log_info("runtime_started", {"name": self.name, "device_id": self.device_id})

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
        self._log_info("server_base_url_configured", {"server_base_url": self.server_base_url})

    def enable_local_camera(self, camera_index: int = 0, preferred_width: int | None = None, preferred_height: int | None = None) -> None:
        """启用本机摄像头。

        参数：
        - camera_index：摄像头编号
        - preferred_width：期望宽度
        - preferred_height：期望高度
        """

        self.sensor_hub.configure_local_camera(
            camera_index=camera_index,
            preferred_width=preferred_width,
            preferred_height=preferred_height,
        )
        self._log_info(
            "local_camera_enabled",
            {"camera_index": camera_index, "preferred_width": preferred_width, "preferred_height": preferred_height},
        )

    def enable_local_microphone(self, sample_rate: int = 16000, channels: int = 1, dtype: str = "int16") -> None:
        """启用本机麦克风。

        参数：
        - sample_rate：采样率
        - channels：声道数
        - dtype：采样数据类型
        """

        self.sensor_hub.configure_local_microphone(
            sample_rate=sample_rate,
            channels=channels,
            dtype=dtype,
        )
        self._log_info("local_microphone_enabled", {"sample_rate": sample_rate, "channels": channels, "dtype": dtype})

    def enable_local_speaker(self) -> None:
        """启用本机喇叭。"""

        self.device_control.enable_local_speaker()
        self._log_info("local_speaker_enabled", self.device_control.get_settings())

    def capture_real_camera_frame(self, output_path: str | None = None) -> dict:
        """采集一帧本机摄像头画面。

        参数：
        - output_path：可选输出路径

        返回值：
        - 摄像头采集结果
        """

        result = self.sensor_hub.capture_local_camera_frame(output_path=output_path)
        self._log_info("local_camera_frame_captured", result)
        return result

    def record_real_microphone_audio(self, duration_sec: float, output_path: str) -> dict:
        """录制一段本机麦克风音频。

        参数：
        - duration_sec：录音时长
        - output_path：输出路径

        返回值：
        - 麦克风录音结果
        """

        result = self.sensor_hub.record_local_microphone_audio(duration_sec=duration_sec, output_path=output_path)
        self._log_info("local_microphone_audio_recorded", result)
        return result

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
            self._log_info("peer_link_connected", {"task_session_id": task_session_id, "ws_url": ws_url})
        except Exception as exc:
            client.close()
            self.gateway.report_broken_peer_link(task_session_id=task_session_id, reason=str(exc))
            self._log_info("peer_link_connect_failed", {"task_session_id": task_session_id, "reason": str(exc), "ws_url": ws_url})
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
        self._log_info("peer_link_stopped", {"task_session_id": task_session_id})
        return {
            "task_session_id": task_session_id,
            "runtime": "glass",
            "status": LinkStatus.CLOSED.value,
        }

    def build_broken_link_payload(self, task_session_id: str, reason: str) -> dict:
        """构造连接异常上报载荷。"""

        self.gateway.report_broken_peer_link(task_session_id=task_session_id, reason=reason)
        self._log_info("peer_link_broken", {"task_session_id": task_session_id, "reason": reason})
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
            self._log_info("frame_analysis_send_failed", {"task_session_id": task_session_id, "reason": str(exc)})
            raise

        hint, execution_feedback = self._execute_find_object_hint_response(task_session_id=task_session_id, response=response)

        self._log_info(
            "frame_analysis_sent",
            {
                "task_session_id": task_session_id,
                "target_name": target_name,
                "analysis": analysis,
                "hint": hint,
                "execution_feedback": execution_feedback,
            },
        )

        return {
            "task_session_id": task_session_id,
            "hint": hint,
            "execution_feedback": execution_feedback,
            "state_summary": response.get("state_summary", {}),
            "status": response.get("status"),
            "phase": response.get("phase"),
        }

    def _execute_find_object_hint_response(self, task_session_id: str, response: Dict[str, Any]):
        """执行手机侧回传的找物引导建议，并避免连续帧重复播报。"""

        hint = response["hint"]
        hint_text = hint["text"]
        now = time.time()
        should_execute = (
            self.last_guidance_texts.get(task_session_id) != hint_text
            or (now - self.last_guidance_ts.get(task_session_id, 0.0)) >= 2.0
        )
        if should_execute:
            execution_feedback = self.executor_bus.submit(
                ExecutionRequest(
                    execution_id=f"exec_{task_session_id}",
                    session_id=task_session_id,
                    execution_type=ExecutionType.SPEECH,
                    priority=TaskPriority.HIGH,
                    payload={"text": hint_text},
                )
            )
            execution_feedback_dict = execution_feedback.to_dict()
            self.last_guidance_texts[task_session_id] = hint_text
            self.last_guidance_ts[task_session_id] = now
            if self.server_base_url:
                post_json(
                    f"{self.server_base_url}/tasks/{task_session_id}/guidance-executed",
                    {
                        "runtime": "glass",
                        "hint_text": hint_text,
                        "execution_feedback": execution_feedback_dict,
                        "state_summary": response.get("state_summary", {}),
                    },
                )
        else:
            execution_feedback_dict = {
                "execution_id": f"exec_{task_session_id}",
                "session_id": task_session_id,
                "accepted": False,
                "status": "deduped",
                "detail": "重复引导已跳过播报",
            }
        return hint, execution_feedback_dict

    def build_voice_event(self, text: str, audio_ref: str, confidence: float):
        """基于当前事件感知模块构造语音事件。"""

        if not self.event_detector.should_emit_voice_event(text, confidence):
            return None
        event = self.event_detector.build_voice_event(text, audio_ref, confidence)
        self._log_info("voice_event_built", event.to_dict())
        return event

    def handle_test_text_input(self, text: str) -> dict:
        """处理测试支持服务注入的文本输入。"""

        event = self.build_voice_event(text=text, audio_ref="test-support://text", confidence=0.99)
        if event is None:
            raise RuntimeError("文本未形成有效语音事件。")
        self.test_inputs["texts"].append(event.to_dict())
        if self.server_base_url is None:
            raise RuntimeError("服务器控制面地址尚未配置。")
        route_result = post_json(f"{self.server_base_url}/events/voice", {"event": event.to_dict()})
        self._log_info("test_text_forwarded", {"text": text, "route_result": route_result})
        return {
            "voice_event": event.to_dict(),
            "route_result": route_result,
        }

    def handle_test_image_input(self, image_path: str) -> dict:
        """处理测试支持服务注入的图片输入。"""

        record = {"image_path": image_path}
        self.test_inputs["images"].append(record)
        self._log_info("test_image_received", record)
        return record

    def stream_video_file(self, task_session_id: str, video_path: str, fps_limit: float = 5.0, target_name: str = "手机") -> dict:
        """通过任务级 WebSocket 流式发送本地视频文件。

        参数：
        - task_session_id：任务实例标识
        - video_path：视频文件路径
        - fps_limit：发送帧率上限

        返回值：
        - 视频流发送结果
        """

        import base64
        import time
        import cv2

        client = self.peer_ws_clients.get(task_session_id)
        if client is None:
            raise RuntimeError(f"任务级连接尚未建立: {task_session_id}")

        capture = cv2.VideoCapture(video_path)
        if not capture.isOpened():
            raise RuntimeError(f"无法打开视频文件: {video_path}")

        frame_index = 0
        sent_frames = 0
        detected_frames = 0
        last_response = None
        started_at = time.time()
        try:
            while True:
                ok, frame = capture.read()
                if not ok or frame is None:
                    break
                encoded_ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                if not encoded_ok:
                    continue
                payload = {
                    "task_session_id": task_session_id,
                    "frame_index": frame_index,
                    "width": int(frame.shape[1]),
                    "height": int(frame.shape[0]),
                    "jpeg_base64": base64.b64encode(encoded.tobytes()).decode("ascii"),
                    "target_name": target_name,
                }
                response = client.request("/stream/frame", payload)
                last_response = response
                if response.get("hint"):
                    _hint, execution_feedback = self._execute_find_object_hint_response(
                        task_session_id=task_session_id,
                        response=response,
                    )
                    if execution_feedback.get("status") != "deduped":
                        detected_frames += 1
                frame_index += 1
                sent_frames += 1
                if fps_limit > 0:
                    time.sleep(1.0 / fps_limit)
        finally:
            capture.release()

        result = {
            "task_session_id": task_session_id,
            "video_path": video_path,
            "sent_frames": sent_frames,
            "detected_frames": detected_frames,
            "elapsed_sec": round(time.time() - started_at, 3),
            "fps_limit": fps_limit,
            "last_response": last_response,
        }
        self._log_info("video_stream_sent", result)
        return result

    def send_image_file_to_peer(self, task_session_id: str, image_path: str, target_name: str = "手机") -> dict:
        """通过任务级 WebSocket 发送一张本地图像文件。

        参数：
        - task_session_id：任务实例标识
        - image_path：图像文件路径

        返回值：
        - 图像发送结果
        """

        import base64
        import cv2

        client = self.peer_ws_clients.get(task_session_id)
        if client is None:
            raise RuntimeError(f"任务级连接尚未建立: {task_session_id}")

        frame = cv2.imread(image_path)
        if frame is None:
            raise RuntimeError(f"无法读取图像文件: {image_path}")
        encoded_ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not encoded_ok:
            raise RuntimeError("图像 JPEG 编码失败。")
        response = client.request(
            "/stream/frame",
            {
                "task_session_id": task_session_id,
                "frame_index": 0,
                "width": int(frame.shape[1]),
                "height": int(frame.shape[0]),
                "jpeg_base64": base64.b64encode(encoded.tobytes()).decode("ascii"),
                "target_name": target_name,
                "mark_completed": True,
            },
        )
        hint, execution_feedback = self._execute_find_object_hint_response(task_session_id=task_session_id, response=response)
        result = {
            "task_session_id": task_session_id,
            "image_path": image_path,
            "response": response,
            "hint": hint,
            "execution_feedback": execution_feedback,
        }
        self._log_info("image_sent_to_peer", result)
        return result

    def stop(self) -> None:
        """停止眼镜端运行时。

        主要逻辑：
        - 当前阶段只保留停止入口，占位后续资源释放逻辑。
        """

        for client in self.peer_ws_clients.values():
            client.close()
        self.peer_ws_clients.clear()
        self.last_guidance_texts.clear()
        self.last_guidance_ts.clear()
        self.gateway.disconnect()
        self._log_info("runtime_stopped", {"name": self.name, "device_id": self.device_id})

    def _log_info(self, action: str, payload: Dict[str, Any]) -> None:
        """记录结构化信息日志。"""

        if not hasattr(self, "logger"):
            return
        self.logger.info("%s %s", action, json.dumps(payload, ensure_ascii=False))
