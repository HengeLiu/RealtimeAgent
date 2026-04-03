"""容器级模拟三端直连服务。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional

from nextgen.apps.glass.runtime.app import GlassRuntimeApp
from nextgen.apps.phone.runtime.app import PhoneRuntimeApp
from nextgen.apps.server.runtime.app import ServerRuntimeApp
from nextgen.integration.container_sim.ws_client import PeerRpcClients
from nextgen.shared.enums.common import CaptureMode, ExecutionType, RuntimeType, SensorType, TaskPriority, TaskStatus
from nextgen.shared.models import (
    BoundingBox,
    CaptureProfile,
    CaptureRequest,
    ExecutionRequest,
    HandObservation,
    ObjectObservation,
    Resolution,
    SourceTargetRef,
    TaskSession,
)


@dataclass
class PeerEndpoints:
    """三端直连地址定义。"""

    server_ws_url: str
    phone_ws_url: str
    glass_ws_url: str


class ContainerGlassService:
    """容器级眼镜服务。"""

    def __init__(self, device_id: str = "glass-001") -> None:
        self.runtime = "glass"
        self.device_id = device_id
        self.app = GlassRuntimeApp(device_id=device_id)
        self.app.start()

    def stop(self) -> None:
        """停止眼镜服务。"""

        self.app.stop()

    def handle_voice_input(self, text: str, audio_ref: str, confidence: float, rpc_clients: PeerRpcClients) -> Dict[str, object]:
        """处理来自联调器的语音输入。"""

        voice_event = self.app.build_voice_event(text=text, audio_ref=audio_ref, confidence=confidence)
        if voice_event is None:
            return {"status": "ignored", "reason": "vad_rejected"}
        server_response = rpc_clients.server.request("/voice-event", {"event": voice_event.to_dict()})
        return {
            "status": "ok",
            "voice_event": voice_event.to_dict(),
            "server_response": server_response,
        }

    def handle_capture_request(self, payload: Dict[str, object], rpc_clients: PeerRpcClients) -> Dict[str, object]:
        """处理服务器下发的采集请求。"""

        request = CaptureRequest(
            request_id=str(payload["request_id"]),
            session_id=str(payload["session_id"]),
            sensor=SensorType(str(payload["sensor"])),
            mode=CaptureMode(str(payload["mode"])),
            priority=TaskPriority(str(payload["priority"])),
            profile=CaptureProfile(
                fps=payload["profile"].get("fps"),  # type: ignore[index]
                resolution=Resolution(
                    width=payload["profile"]["resolution"]["width"],  # type: ignore[index]
                    height=payload["profile"]["resolution"]["height"],  # type: ignore[index]
                ),
                quality=payload["profile"].get("quality"),  # type: ignore[index]
                duration_ms=payload["profile"].get("duration_ms"),  # type: ignore[index]
                extra=payload["profile"].get("extra", {}),  # type: ignore[index]
            ),
            consumer=SourceTargetRef(
                runtime=RuntimeType(str(payload["consumer"]["runtime"])),  # type: ignore[index]
                device_id=str(payload["consumer"]["device_id"]),  # type: ignore[index]
                component=payload["consumer"].get("component"),  # type: ignore[index]
            ),
        )
        grant = self.app.sensor_hub.register_capture_request(request)
        server_ack = rpc_clients.server.request(
            "/capture-granted",
            {"session_id": request.session_id, "grant": grant.to_dict()},
        )
        return {"grant": grant.to_dict(), "server_ack": server_ack}

    def handle_guidance_hint(self, session_id: str, text: str, rpc_clients: PeerRpcClients) -> Dict[str, object]:
        """处理手机直发的引导建议。"""

        feedback = self.app.executor_bus.submit(
            ExecutionRequest(
                execution_id=f"exec_{session_id}",
                session_id=session_id,
                execution_type=ExecutionType.SPEECH,
                priority=TaskPriority.HIGH,
                payload={"text": text},
            )
        )
        server_ack = rpc_clients.server.request(
            "/guidance-executed",
            {
                "session_id": session_id,
                "text": text,
                "execution_feedback": feedback.to_dict(),
            },
        )
        return {
            "execution_feedback": feedback.to_dict(),
            "server_ack": server_ack,
        }

    def handle_capture_release(self, request_id: str, session_id: str) -> Dict[str, object]:
        """处理服务器发来的采集释放请求。"""

        self.app.sensor_hub.cancel_capture_request(request_id)
        return {"status": "released", "request_id": request_id, "session_id": session_id}


class ContainerPhoneService:
    """容器级手机服务。"""

    def __init__(self, device_id: str = "phone-001") -> None:
        self.runtime = "phone"
        self.device_id = device_id
        self.app = PhoneRuntimeApp()
        self.app.start()

    def stop(self) -> None:
        """停止手机服务。"""

        self.app.stop()

    def handle_task_start(self, session_id: str, target_name: str) -> Dict[str, object]:
        """处理服务器下发的找物启动请求。"""

        session = TaskSession(
            session_id=session_id,
            task_name="find_object",
            status=TaskStatus.STARTING,
            phase="waiting_stream",
            priority=TaskPriority.HIGH,
            created_at=datetime.now().astimezone().isoformat(),
            updated_at=datetime.now().astimezone().isoformat(),
            initiator=SourceTargetRef(runtime=RuntimeType.SERVER, device_id="server-main"),
            participants={"phone": ["find_object_task"]},
            input={"target_name": target_name},
        )
        self.app.local_task_center.create_session(session)
        return {"status": "accepted", "session_id": session_id}

    def handle_frame_analysis(
        self,
        payload: Dict[str, object],
        rpc_clients: PeerRpcClients,
    ) -> Dict[str, object]:
        """处理联调器送入的单帧分析输入。"""

        session_id = str(payload["session_id"])
        target_name = str(payload["target_name"])
        candidates = [
            ObjectObservation(
                center_x=item["center_x"],
                center_y=item["center_y"],
                area=item["area"],
                polygon=item.get("polygon", []),
                score=item.get("score", 0.0),
                position=item.get("position", "unknown"),
            )
            for item in payload.get("candidates", [])
        ]
        hand_payload = payload.get("hand_observation")
        hand_observation: Optional[HandObservation] = None
        if hand_payload is not None:
            hand_observation = HandObservation(
                center_x=hand_payload["center_x"],
                center_y=hand_payload["center_y"],
                area=hand_payload["area"],
                bbox=BoundingBox(
                    x1=hand_payload["bbox"]["x1"],
                    y1=hand_payload["bbox"]["y1"],
                    x2=hand_payload["bbox"]["x2"],
                    y2=hand_payload["bbox"]["y2"],
                ),
                grasp_detected=hand_payload.get("grasp_detected", False),
                grasp_score=hand_payload.get("grasp_score", 0.0),
            )
        hint = self.app.analyze_find_object_frame(
            session_id=session_id,
            target_name=target_name,
            candidates=candidates,
            hand_observation=hand_observation,
        )
        self.app.local_task_center.transition_session(
            session_id=session_id,
            status=TaskStatus.RUNNING,
            phase="guiding",
            summary={"hint_text": hint.text},
        )
        guidance_result = rpc_clients.glass.request(
            "/guidance-hint",
            {"session_id": session_id, "text": hint.text},
        )
        status_result = rpc_clients.server.request(
            "/task-status",
            {
                "session_id": session_id,
                "status": "running",
                "phase": "guiding",
                "hint_text": hint.text,
            },
        )
        completed_result = None
        final_status = "running"
        if payload.get("mark_completed", True):
            self.app.local_task_center.finish_session(session_id)
            completed_result = rpc_clients.server.request(
                "/task-completed",
                {
                    "session_id": session_id,
                    "target_name": target_name,
                    "hint_text": hint.text,
                },
            )
            final_status = "completed"
        return {
            "hint": hint.to_dict(),
            "guidance_result": guidance_result,
            "status_result": status_result,
            "completed_result": completed_result,
            "final_status": final_status,
        }


class ContainerServerService:
    """容器级服务器服务。"""

    def __init__(self, device_id: str = "server-main") -> None:
        self.runtime = "server"
        self.device_id = device_id
        self.app = ServerRuntimeApp()
        self.app.start()
        self.app.event_router.enable_keyword_dispatch = True
        self.latest_session_id: Optional[str] = None

    def stop(self) -> None:
        """停止服务器服务。"""

        self.app.stop()

    def handle_voice_event(self, event: Dict[str, object], rpc_clients: PeerRpcClients) -> Dict[str, object]:
        """处理眼镜上送的结构化语音事件。"""

        dispatch_result = self.app.event_router.route(event)["dispatch_result"]
        if dispatch_result.get("status") == "ignored":
            return dispatch_result
        session_id = dispatch_result["session_id"]
        target_name = dispatch_result["params"]["target_name"]
        self.latest_session_id = session_id
        capture_request = CaptureRequest(
            request_id=f"capreq_{session_id}",
            session_id=session_id,
            sensor=SensorType.RGB_CAMERA,
            mode=CaptureMode.STREAM,
            priority=TaskPriority.HIGH,
            profile=CaptureProfile(
                fps=5,
                resolution=Resolution(width=1280, height=720),
                quality="high",
            ),
            consumer=SourceTargetRef(runtime=RuntimeType.PHONE, device_id="phone-001"),
        )
        capture_response = rpc_clients.glass.request("/capture-request", capture_request.to_dict())
        phone_response = rpc_clients.phone.request(
            "/task-start",
            {
                "session_id": session_id,
                "task_name": "find_object",
                "target_name": target_name,
            },
        )
        return {
            "status": "ok",
            "session_id": session_id,
            "target_name": target_name,
            "dispatch_result": dispatch_result,
            "capture_request": capture_request.to_dict(),
            "capture_response": capture_response,
            "phone_response": phone_response,
        }

    def handle_capture_granted(self, session_id: str, grant: Dict[str, object]) -> Dict[str, object]:
        """处理眼镜回传的采集授权结果。"""

        self.app.state_log_store.append_task_event(
            session_id=session_id,
            event={"event_name": "capture_request_granted", "grant": grant},
        )
        self.app.background_task_center.transition_session(
            session_id=session_id,
            status=TaskStatus.RUNNING,
            phase="streaming",
            summary={"grant": grant},
        )
        return {"status": "ok", "session_id": session_id}

    def handle_task_status(self, session_id: str, payload: Dict[str, object]) -> Dict[str, object]:
        """处理手机回传的任务状态。"""

        self.app.state_log_store.append_task_event(
            session_id=session_id,
            event={"event_name": "phone_status_reported", **payload},
        )
        self.app.background_task_center.transition_session(
            session_id=session_id,
            status=TaskStatus.RUNNING,
            phase=str(payload.get("phase", "guiding")),
            summary={"hint_text": payload.get("hint_text", "")},
        )
        return {"status": "ok", "session_id": session_id}

    def handle_guidance_executed(self, session_id: str, payload: Dict[str, object]) -> Dict[str, object]:
        """处理眼镜回传的引导执行结果。"""

        self.app.state_log_store.append_task_event(
            session_id=session_id,
            event={"event_name": "guidance_executed", **payload},
        )
        return {"status": "ok", "session_id": session_id}

    def handle_task_completed(
        self,
        session_id: str,
        payload: Dict[str, object],
        rpc_clients: PeerRpcClients,
    ) -> Dict[str, object]:
        """处理手机回传的任务完成事件。"""

        self.app.state_log_store.append_task_event(
            session_id=session_id,
            event={"event_name": "find_object_completed", **payload},
        )
        self.app.background_task_center.transition_session(
            session_id=session_id,
            status=TaskStatus.COMPLETED,
            phase="completed",
            summary={"target_name": payload.get("target_name")},
            result={"hint_text": payload.get("hint_text")},
        )
        request_id = f"capreq_{session_id}"
        release_response = rpc_clients.glass.request(
            "/capture-release",
            {"request_id": request_id, "session_id": session_id},
        )
        return {"status": "ok", "session_id": session_id, "release_response": release_response}

    def handle_frame_analysis(self, payload: Dict[str, object], rpc_clients: PeerRpcClients) -> Dict[str, object]:
        """处理联调器送入的找物单帧分析输入，并转发给手机。"""

        phone_response = rpc_clients.phone.request("/frame-analysis", payload)
        return {"status": "ok", "phone_response": phone_response}

    def get_session_report(self, session_id: str) -> Dict[str, object]:
        """获取指定任务实例的当前报告。"""

        session = self.app.background_task_center.get_session(session_id)
        snapshot = self.app.state_log_store.get_task_snapshot(session_id)
        return {
            "session": session.to_dict() if session else None,
            "task_snapshot": snapshot,
            "recent_logs": self.app.state_log_store.get_recent_records(limit=20),
        }
