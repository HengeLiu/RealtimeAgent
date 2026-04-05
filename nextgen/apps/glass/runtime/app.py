"""眼镜端运行时应用实现。"""

import json
import logging
import os
import time
import threading
import uuid
import tempfile
import base64
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
from nextgen.shared.utils.http import get_json, post_json
from nextgen.shared.utils.ws_rpc import WebSocketRpcClient, wait_for_ws_ready
from websockets.sync.client import connect

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
    ACTION_LABELS = {
        "runtime_started": "眼镜运行时启动",
        "runtime_stopped": "眼镜运行时停止",
        "server_base_url_configured": "配置服务器地址",
        "local_camera_enabled": "启用本机摄像头",
        "local_microphone_enabled": "启用本机麦克风",
        "local_speaker_enabled": "启用本机喇叭",
        "local_camera_frame_captured": "采集摄像头画面",
        "local_microphone_audio_recorded": "完成麦克风录音",
        "peer_link_connected": "建立任务级长连接",
        "peer_link_connect_failed": "任务级长连接失败",
        "peer_link_stopped": "关闭任务级长连接",
        "peer_link_broken": "上报长连接断开",
        "frame_analysis_sent": "发送找物分析结果",
        "frame_analysis_send_failed": "发送找物分析失败",
        "push_to_talk_recording_started": "开始对讲录音",
        "push_to_talk_recording_stopped": "结束对讲录音并发送",
        "realtime_voice_started": "开始实时对话",
        "realtime_voice_stopped": "结束实时对话",
        "voice_tts_chunk_played": "播放流式语音音频块",
        "voice_tts_stopped": "停止语音播放",
        "voice_server_message_received": "收到语音会话消息",
        "voice_ws_closed": "语音会话连接关闭",
        "stream_frame_detected": "处理原始图像帧",
        "video_stream_sent": "开始或完成视频流发送",
        "image_sent_to_peer": "发送单张图片到手机",
        "test_text_forwarded": "转发测试文本到服务器",
        "test_image_received": "收到测试图片",
    }

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
        self.last_guidance_texts: Dict[str, str] = {}
        self.last_guidance_ts: Dict[str, float] = {}
        self.voice_sessions: Dict[str, Dict[str, Any]] = {}
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

    def build_ui_snapshot(self) -> dict:
        """构造眼镜独立 UI 所需的状态快照。"""

        server_snapshot = None
        if self.server_base_url:
            try:
                server_snapshot = get_json(f"{self.server_base_url}/snapshot")
            except Exception:
                server_snapshot = None

        return {
            "device_id": self.device_id,
            "server_base_url": self.server_base_url,
            "sensor_inputs": self.sensor_hub.build_input_snapshot(),
            "peer_sessions": self.gateway.list_peer_sessions(),
            "voice_sessions": list(self.voice_sessions.keys()),
            "server_snapshot": server_snapshot,
        }

    def create_find_object_peer_link(self, phone_device_id: str = "phone-001", target_name: str = "手机") -> dict:
        """通过服务器创建一条找物任务级长连接。"""

        if self.server_base_url is None:
            raise RuntimeError("服务器控制面地址尚未配置。")
        created = post_json(
            f"{self.server_base_url}/tasks/create-session",
            {
                "task_name": "find_object",
                "glass_device_id": self.device_id,
                "phone_device_id": phone_device_id,
                "input": {"target_name": target_name},
            },
        )
        session_id = created["session"]["session_id"]
        orchestrated = post_json(
            f"{self.server_base_url}/tasks/{session_id}/peer-link/orchestrate",
            {"stream_type": "image_stream"},
        )
        self._log_info(
            "create_find_object_peer_link",
            {"session_id": session_id, "phone_device_id": phone_device_id, "target_name": target_name},
        )
        return {"created": created, "orchestrated": orchestrated}

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

    def start_push_to_talk_recording(self, session_id: str | None = None) -> dict:
        """启动对讲模式录音。"""

        if session_id is None:
            session_id = f"ptt_{uuid.uuid4().hex[:8]}"
        output_path = os.path.join(tempfile.gettempdir(), f"{session_id}.wav")
        result = self.sensor_hub.start_local_microphone_recording(output_path=output_path)
        self.voice_sessions[session_id] = {
            "mode": "push_to_talk",
            "recording": True,
            "audio_path": output_path,
        }
        self._log_info("push_to_talk_recording_started", {"session_id": session_id, **result})
        return {"session_id": session_id, **result}

    def stop_push_to_talk_recording_and_dispatch(self, session_id: str) -> dict:
        """停止对讲录音、上传服务器处理，并通过语音 WS 接收 TTS 音频。"""

        if self.server_base_url is None:
            raise RuntimeError("服务器控制面地址尚未配置。")
        session = self.voice_sessions.get(session_id)
        if session is None:
            raise KeyError(f"对讲录音会话不存在: {session_id}")
        recording = self.sensor_hub.stop_local_microphone_recording()
        voice_session = post_json(
            f"{self.server_base_url}/voice/sessions",
            {"device_id": self.device_id, "mode": "push_to_talk"},
        )["session"]
        voice_session_id = voice_session["session_id"]
        ws_url = self._build_server_voice_ws_url(voice_session_id)
        self._open_voice_ws_session(voice_session_id=voice_session_id, ws_url=ws_url, mode="push_to_talk")
        processed = post_json(
            f"{self.server_base_url}/voice/sessions/{voice_session_id}/push-to-talk",
            {"audio_path": recording["output_path"]},
        )
        session.update(
            {
                "recording": False,
                "server_voice_session_id": voice_session_id,
                "audio_path": recording["output_path"],
            }
        )
        self._log_info(
            "push_to_talk_recording_stopped",
            {
                "session_id": session_id,
                "voice_session_id": voice_session_id,
                "recording": recording,
                "processed": processed,
            },
        )
        return {
            "session_id": session_id,
            "voice_session_id": voice_session_id,
            "recording": recording,
            "processed": processed,
        }

    def start_realtime_voice_conversation(self) -> dict:
        """启动实时语音对话。"""

        if self.server_base_url is None:
            raise RuntimeError("服务器控制面地址尚未配置。")
        voice_session = post_json(
            f"{self.server_base_url}/voice/sessions",
            {"device_id": self.device_id, "mode": "realtime"},
        )["session"]
        voice_session_id = voice_session["session_id"]
        ws_url = self._build_server_voice_ws_url(voice_session_id)
        state = self._open_voice_ws_session(voice_session_id=voice_session_id, ws_url=ws_url, mode="realtime")

        def _on_chunk(chunk_bytes: bytes) -> None:
            if state.get("closed"):
                return
            try:
                with state["send_lock"]:
                    state["connection"].send(
                        json.dumps(
                            {
                                "type": "audio.chunk",
                                "session_id": voice_session_id,
                                "audio_base64": base64.b64encode(chunk_bytes).decode("ascii"),
                            },
                            ensure_ascii=False,
                        )
                    )
            except Exception as exc:
                self._log_info("realtime_audio_send_failed", {"session_id": voice_session_id, "reason": str(exc)})

        state["microphone_stream"] = self.sensor_hub.start_local_microphone_stream(on_chunk=_on_chunk, blocksize=1600)
        self._log_info("realtime_voice_started", {"voice_session_id": voice_session_id, "ws_url": ws_url})
        return {"voice_session_id": voice_session_id, "ws_url": ws_url}

    def stop_realtime_voice_conversation(self, voice_session_id: str) -> dict:
        """停止实时语音对话。"""

        state = self.voice_sessions.get(voice_session_id)
        if state is None:
            raise KeyError(f"实时语音会话不存在: {voice_session_id}")
        microphone_stream = state.get("microphone_stream")
        if microphone_stream is not None:
            microphone_stream.stop()
            microphone_stream.close()
        connection = state.get("connection")
        if connection is not None:
            try:
                with state["send_lock"]:
                    connection.send(json.dumps({"type": "session.close", "session_id": voice_session_id}, ensure_ascii=False))
            except Exception:
                pass
            try:
                connection.close()
            except Exception:
                pass
        state["closed"] = True
        self.device_control.stop_audio_playback()
        self._log_info("realtime_voice_stopped", {"voice_session_id": voice_session_id})
        return {"voice_session_id": voice_session_id, "status": "stopped"}

    def _build_server_voice_ws_url(self, voice_session_id: str) -> str:
        http_base = self.server_base_url.rstrip("/")
        if http_base.startswith("https://"):
            return http_base.replace("https://", "wss://", 1) + f"/voice/ws/{voice_session_id}"
        return http_base.replace("http://", "ws://", 1) + f"/voice/ws/{voice_session_id}"

    def _open_voice_ws_session(self, voice_session_id: str, ws_url: str, mode: str) -> Dict[str, Any]:
        connection = connect(ws_url, open_timeout=5, close_timeout=1, max_size=2**22)
        state = {
            "voice_session_id": voice_session_id,
            "mode": mode,
            "connection": connection,
            "send_lock": threading.Lock(),
            "closed": False,
            "microphone_stream": None,
        }
        self.voice_sessions[voice_session_id] = state

        def _receiver() -> None:
            try:
                while not state["closed"]:
                    raw = connection.recv()
                    message = json.loads(raw)
                    self._handle_voice_server_message(voice_session_id, message)
            except Exception as exc:
                self._log_info("voice_ws_closed", {"voice_session_id": voice_session_id, "reason": str(exc)})
            finally:
                state["closed"] = True

        state["receiver_thread"] = threading.Thread(target=_receiver, daemon=True)
        state["receiver_thread"].start()
        return state

    def _handle_voice_server_message(self, voice_session_id: str, message: Dict[str, Any]) -> None:
        message_type = message.get("type")
        if message_type == "tts.audio.chunk":
            audio_bytes = base64.b64decode(message["audio_base64"].encode("ascii"))
            result = self.device_control.play_audio_chunk(audio_bytes, sample_rate=int(message.get("sample_rate", 16000)))
            self._log_info(
                "voice_tts_chunk_played",
                {"voice_session_id": voice_session_id, "chunk_size": len(audio_bytes), "playback": result},
            )
        elif message_type == "tts.stop":
            result = self.device_control.stop_audio_playback()
            self._log_info("voice_tts_stopped", {"voice_session_id": voice_session_id, "playback": result})
        else:
            self._log_info("voice_server_message_received", {"voice_session_id": voice_session_id, "message": message})

    def handle_test_text_input(self, text: str) -> dict:
        """处理 UI 注入的文本输入。"""

        event = self.build_voice_event(text=text, audio_ref="test-support://text", confidence=0.99)
        if event is None:
            raise RuntimeError("文本未形成有效语音事件。")
        input_record = self.sensor_hub.inject_ui_text(text)
        if self.server_base_url is None:
            raise RuntimeError("服务器控制面地址尚未配置。")
        route_result = post_json(f"{self.server_base_url}/events/voice", {"event": event.to_dict()})
        self._log_info("test_text_forwarded", {"text": text, "route_result": route_result, "input_record": input_record})
        return {
            "input_record": input_record,
            "voice_event": event.to_dict(),
            "route_result": route_result,
        }

    def handle_test_image_input(self, image_path: str) -> dict:
        """处理 UI 注入的图片输入。"""

        record = self.sensor_hub.inject_ui_image(image_path)
        self._log_info("test_image_received", record)
        return record

    def handle_test_video_input(self, video_path: str) -> dict:
        """处理 UI 注入的视频输入。"""

        record = self.sensor_hub.inject_ui_video(video_path)
        self._log_info("test_video_received", record)
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
        for voice_session_id in list(self.voice_sessions.keys()):
            state = self.voice_sessions.get(voice_session_id, {})
            if state.get("mode") == "realtime":
                try:
                    self.stop_realtime_voice_conversation(voice_session_id)
                except Exception:
                    pass
            else:
                connection = state.get("connection")
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass
        self.voice_sessions.clear()
        self.last_guidance_texts.clear()
        self.last_guidance_ts.clear()
        self.gateway.disconnect()
        self._log_info("runtime_stopped", {"name": self.name, "device_id": self.device_id})

    def _log_info(self, action: str, payload: Dict[str, Any]) -> None:
        """记录结构化信息日志。"""

        if not hasattr(self, "logger"):
            return
        label = self.ACTION_LABELS.get(action, action)
        self.logger.info("%s(%s) %s", label, action, json.dumps(payload, ensure_ascii=False))
