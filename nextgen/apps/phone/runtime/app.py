"""手机端运行时应用实现。"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict

from nextgen.apps.phone.gateway.phone_gateway import PhoneGateway
from nextgen.apps.phone.skills.object_detection_skill import ObjectDetectionSkill
from nextgen.apps.phone.tasks.find_object_task import FindObjectTask
from nextgen.apps.phone.tasks.local_task_center import LocalTaskCenter
from nextgen.shared.enums.common import CapabilityType, LinkStatus, RuntimeType, TaskPriority, TaskStatus
from nextgen.shared.models.base import SourceTargetRef
from nextgen.shared.models.control import NodeEndpoint
from nextgen.shared.models.detection import BoundingBox, FindObjectFrameAnalysis, HandObservation, ObjectObservation
from nextgen.shared.models.task import TaskSession
from nextgen.shared.utils.http import post_json

@dataclass
class PhoneRuntimeApp:
    """手机端运行时应用。"""

    name: str = "phone-runtime"
    device_id: str = "phone-001"

    def start(self) -> None:
        """启动手机端运行时。

        主要逻辑：
        - 当前阶段完成最小模块装配，便于后续扩展真实连接与任务装配。
        """

        self.logger = logging.getLogger("nextgen.phone.runtime")
        self.gateway = PhoneGateway()
        self.gateway.device_id = self.device_id
        self.local_task_center = LocalTaskCenter()
        self.find_object_task = FindObjectTask(target_name="未设置")
        self.object_detection_skill = ObjectDetectionSkill()
        self.server_base_url: str | None = None
        self.gateway.connect()
        self._log_info("runtime_started", {"name": self.name, "device_id": self.device_id})

    def configure_control_endpoint(self, host: str, port: int, scheme: str = "http", base_path: str = "/device-api") -> None:
        """配置手机控制面地址。"""

        self.gateway.update_control_endpoint(NodeEndpoint(host=host, port=port, scheme=scheme, base_path=base_path))

    def build_registration_payload(self) -> dict:
        """构造手机注册载荷。"""

        registration = self.gateway.build_registration(
            display_name="手机",
            capabilities=[
                CapabilityType.LOCAL_DETECTION,
                CapabilityType.OCR,
                CapabilityType.MAP_NAVIGATION,
            ],
        )
        return registration.to_dict()

    def build_heartbeat_payload(self, status: str = "ready") -> dict:
        """构造手机心跳载荷。"""

        return self.gateway.build_heartbeat(status=status).to_dict()

    def configure_server_base_url(self, server_base_url: str) -> None:
        """配置服务器控制面地址。"""

        self.server_base_url = server_base_url.rstrip("/")
        self._log_info("server_base_url_configured", {"server_base_url": self.server_base_url})

    def handle_prepare_peer_link(self, task_session_id: str, peer_device_id: str, stream_type: str) -> dict:
        """处理服务器下发的准备连接命令。"""

        session = self.gateway.prepare_peer_link_listener(
            task_session_id=task_session_id,
            peer_device_id=peer_device_id,
            stream_type=stream_type,
        )
        if self.local_task_center.get_session(task_session_id) is None:
            now = datetime.now().astimezone().isoformat()
            self.local_task_center.create_session(
                TaskSession(
                    session_id=task_session_id,
                    task_name="find_object",
                    status=TaskStatus.STARTING,
                    phase="preparing_peer_link",
                    priority=TaskPriority.HIGH,
                    created_at=now,
                    updated_at=now,
                    initiator=SourceTargetRef(runtime=RuntimeType.SERVER, device_id="server-main"),
                    participants={"phone": ["local_task", "peer_link_listener"]},
                    input={"peer_device_id": peer_device_id, "stream_type": stream_type},
                )
            )
        self._log_info("peer_link_listener_prepared", {"task_session_id": task_session_id, "session": session})
        return {
            "task_session_id": task_session_id,
            "runtime": "phone",
            "status": LinkStatus.LISTENING.value,
            "listen_endpoint": session["listen_endpoint"],
        }

    def handle_stop_peer_link(self, task_session_id: str) -> dict:
        """处理停止任务级连接命令。"""

        self.gateway.close_peer_session(task_session_id)
        session = self.local_task_center.get_session(task_session_id)
        if session is not None:
            self.local_task_center.transition_session(
                session_id=task_session_id,
                status=TaskStatus.COMPLETED,
                phase="peer_link_closed",
                summary={"status": "closed"},
            )
        return {
            "task_session_id": task_session_id,
            "runtime": "phone",
            "status": LinkStatus.CLOSED.value,
        }

    def handle_peer_stream_connected(self, task_session_id: str) -> dict:
        """处理任务级 WebSocket 已连通。"""

        session = self.gateway.peer_sessions.setdefault(task_session_id, {"session_id": task_session_id})
        session["status"] = LinkStatus.CONNECTED.value
        local_session = self.local_task_center.get_session(task_session_id)
        if local_session is not None:
            self.local_task_center.transition_session(
                session_id=task_session_id,
                status=TaskStatus.RUNNING,
                phase="stream_connected",
                summary={"status": "connected"},
            )
        self._log_info("peer_stream_connected", {"task_session_id": task_session_id})
        return session

    def handle_peer_stream_closed(self, task_session_id: str) -> dict:
        """处理任务级 WebSocket 关闭。"""

        session = self.gateway.peer_sessions.setdefault(task_session_id, {"session_id": task_session_id})
        session["status"] = LinkStatus.CLOSED.value
        self._log_info("peer_stream_closed", {"task_session_id": task_session_id})
        return session

    def build_find_object_analysis(
        self,
        target_name: str,
        analysis_payload: Dict[str, Any],
    ) -> FindObjectFrameAnalysis:
        """从消息载荷构造找物单帧分析对象。"""

        object_observation = None
        raw_object = analysis_payload.get("object_observation")
        if raw_object is not None:
            object_observation = ObjectObservation(
                center_x=float(raw_object["center_x"]),
                center_y=float(raw_object["center_y"]),
                area=float(raw_object["area"]),
                polygon=[[float(point[0]), float(point[1])] for point in raw_object.get("polygon", [])],
                score=float(raw_object.get("score", 0.0)),
                position=str(raw_object.get("position", "unknown")),
            )

        hand_observation = None
        raw_hand = analysis_payload.get("hand_observation")
        if raw_hand is not None:
            hand_observation = HandObservation(
                center_x=float(raw_hand["center_x"]),
                center_y=float(raw_hand["center_y"]),
                area=float(raw_hand["area"]),
                bbox=BoundingBox(**raw_hand["bbox"]),
                grasp_detected=bool(raw_hand.get("grasp_detected", False)),
                grasp_score=float(raw_hand.get("grasp_score", 0.0)),
            )

        return FindObjectFrameAnalysis(
            frame_width=int(analysis_payload["frame_width"]),
            frame_height=int(analysis_payload["frame_height"]),
            target_name=target_name,
            found=bool(analysis_payload["found"]),
            object_observation=object_observation,
            hand_observation=hand_observation,
            candidate_count=int(analysis_payload.get("candidate_count", 0)),
            source=str(analysis_payload.get("source", "peer_ws")),
        )

    def handle_find_object_frame_message(self, task_session_id: str, payload: Dict[str, Any]) -> dict:
        """处理来自眼镜端的找物单帧消息。"""

        target_name = str(payload.get("target_name", "未设置"))
        analysis = self.build_find_object_analysis(target_name=target_name, analysis_payload=payload["analysis"])
        self.find_object_task.target_name = target_name
        hint = self.find_object_task.update_from_frame_analysis(
            session_id=task_session_id,
            analysis=analysis,
        )

        found = bool(analysis.found)
        phase = "guiding" if found else "scanning"
        status = TaskStatus.COMPLETED if payload.get("mark_completed", False) else TaskStatus.RUNNING
        if status == TaskStatus.COMPLETED:
            phase = "completed"
        summary = {
            "target_name": target_name,
            "found": found,
            "position": analysis.object_observation.position if analysis.object_observation else "unknown",
            "phase": phase,
        }
        local_session = self.local_task_center.get_session(task_session_id)
        if local_session is None:
            now = datetime.now().astimezone().isoformat()
            self.local_task_center.create_session(
                TaskSession(
                    session_id=task_session_id,
                    task_name="find_object",
                    status=TaskStatus.RUNNING,
                    phase=phase,
                    priority=TaskPriority.HIGH,
                    created_at=now,
                    updated_at=now,
                    initiator=SourceTargetRef(runtime=RuntimeType.SERVER, device_id="server-main"),
                    participants={"phone": ["find_object_task"]},
                    input={"target_name": target_name},
                    last_state_summary=summary,
                )
            )
        else:
            self.local_task_center.transition_session(
                session_id=task_session_id,
                status=status,
                phase=phase,
                summary=summary,
                result={"target_name": target_name} if status == TaskStatus.COMPLETED else None,
            )

        if self.server_base_url:
            post_json(
                f"{self.server_base_url}/tasks/{task_session_id}/state",
                {
                    "runtime": "phone",
                    "status": status.value,
                    "phase": phase,
                    "summary": summary,
                    "result": {"target_name": target_name} if status == TaskStatus.COMPLETED else None,
                },
            )

        self._log_info(
            "find_object_frame_processed",
            {"task_session_id": task_session_id, "target_name": target_name, "summary": summary, "hint": hint.to_dict()},
        )

        return {
            "task_session_id": task_session_id,
            "hint": hint.to_dict(),
            "state_summary": summary,
            "status": status.value,
            "phase": phase,
        }

    def analyze_find_object_frame(
        self,
        session_id: str,
        target_name: str,
        candidates,
        hand_observation=None,
    ):
        """执行一次手机侧找物单帧分析。"""

        analysis = self.object_detection_skill.build_frame_analysis(
            frame_width=320,
            frame_height=240,
            target_name=target_name,
            candidates=candidates,
            hand_observation=hand_observation,
        )
        self.find_object_task.target_name = target_name
        return self.find_object_task.update_from_frame_analysis(session_id=session_id, analysis=analysis)

    def stop(self) -> None:
        """停止手机端运行时。"""

        self.gateway.disconnect()
        self._log_info("runtime_stopped", {"name": self.name, "device_id": self.device_id})

    def _log_info(self, action: str, payload: Dict[str, Any]) -> None:
        """记录结构化信息日志。"""

        if not hasattr(self, "logger"):
            return
        self.logger.info("%s %s", action, json.dumps(payload, ensure_ascii=False))
