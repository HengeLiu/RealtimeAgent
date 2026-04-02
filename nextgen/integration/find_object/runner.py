"""寻找物体黄金链路集成运行器。"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from nextgen.apps.glass.runtime.app import GlassRuntimeApp
from nextgen.apps.phone.runtime.app import PhoneRuntimeApp
from nextgen.apps.server.runtime.app import ServerRuntimeApp
from nextgen.shared.enums.common import CaptureMode, ExecutionType, RuntimeType, SensorType, TaskPriority, TaskStatus
from nextgen.shared.models import (
    CaptureProfile,
    CaptureRequest,
    ExecutionRequest,
    ObjectObservation,
    Resolution,
    SourceTargetRef,
    TaskSession,
)


@dataclass
class FindObjectRunResult:
    """寻找物体单次运行结果。"""

    session_id: str
    task_name: str
    target_name: str
    voice_event: Dict[str, Any]
    task_create_result: Dict[str, Any]
    capture_grant: Dict[str, Any]
    phone_hint: Dict[str, Any]
    execution_feedback: Dict[str, Any]
    task_status_snapshot: Dict[str, Any]
    task_log_snapshot: Dict[str, Any]
    final_status: str
    final_phase: str
    server_recent_logs: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """将运行结果转换为字典。"""

        return {
            "session_id": self.session_id,
            "task_name": self.task_name,
            "target_name": self.target_name,
            "voice_event": self.voice_event,
            "task_create_result": self.task_create_result,
            "capture_grant": self.capture_grant,
            "phone_hint": self.phone_hint,
            "execution_feedback": self.execution_feedback,
            "task_status_snapshot": self.task_status_snapshot,
            "task_log_snapshot": self.task_log_snapshot,
            "final_status": self.final_status,
            "final_phase": self.final_phase,
            "server_recent_logs": self.server_recent_logs,
        }


class FindObjectIntegrationRunner:
    """寻找物体黄金链路集成运行器。

    主要功能：
    - 串联眼镜、手机、服务器三个运行时
    - 模拟用户语音触发找物任务
    - 模拟手机侧目标检测并直接向眼镜发送引导建议
    - 让服务器只记录任务状态和生命周期日志
    """

    def __init__(self) -> None:
        """初始化集成运行器。"""

        self.glass = GlassRuntimeApp()
        self.phone = PhoneRuntimeApp()
        self.server = ServerRuntimeApp()

    def start(self) -> None:
        """启动三端运行时。"""

        self.glass.start()
        self.phone.start()
        self.server.start()
        self.server.event_router.enable_keyword_dispatch = True
        self.server.gateway.register_client(runtime="glass", device_id=self.glass.device_id)
        self.server.gateway.register_client(runtime="phone", device_id="phone-001")

    def stop(self) -> None:
        """停止三端运行时。"""

        self.glass.stop()
        self.phone.stop()
        self.server.stop()

    def run_find_object(
        self,
        voice_text: str,
        target_name: str,
        candidates: Sequence[ObjectObservation],
        hand_observation=None,
        mark_completed: bool = True,
    ) -> FindObjectRunResult:
        """执行一次寻找物体完整链路。

        主要逻辑：
        1. 眼镜侧形成结构化语音事件
        2. 服务器侧创建混合任务
        3. 眼镜注册相机采集请求
        4. 手机生成引导建议并直发眼镜
        5. 眼镜执行播报
        6. 服务器更新任务状态并收尾
        """

        voice_event = self.glass.build_voice_event(
            text=voice_text,
            audio_ref="audio://find-object-input",
            confidence=0.95,
        )
        if voice_event is None:
            raise RuntimeError("未能形成结构化语音事件。")

        dispatch_result = self.server.event_router.route(voice_event.to_dict())["dispatch_result"]
        session_id = dispatch_result["session_id"]

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
        capture_grant = self.glass.sensor_hub.register_capture_request(capture_request)
        self.server.state_log_store.append_task_event(
            session_id=session_id,
            event={
                "event_name": "capture_request_granted",
                "grant": capture_grant.to_dict(),
            },
        )
        self.server.background_task_center.transition_session(
            session_id=session_id,
            status=TaskStatus.RUNNING,
            phase="streaming",
            summary={"capture_request_id": capture_request.request_id},
        )
        self.phone.local_task_center.create_session(
            TaskSession(
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
        )
        self.phone.local_task_center.transition_session(
            session_id=session_id,
            status=TaskStatus.RUNNING,
            phase="analyzing",
            summary={"candidate_count": len([candidate for candidate in candidates if candidate is not None])},
        )

        analysis = self.phone.object_detection_skill.build_frame_analysis(
            frame_width=320,
            frame_height=240,
            target_name=target_name,
            candidates=candidates,
            hand_observation=hand_observation,
        )
        self.phone.find_object_task.target_name = target_name
        phone_hint = self.phone.find_object_task.update_from_frame_analysis(
            session_id=session_id,
            analysis=analysis,
        )
        self.phone.gateway.send(
            {
                "message_type": "find_object.guidance",
                "session_id": session_id,
                "text": phone_hint.text,
            }
        )

        execution_feedback = self.glass.executor_bus.submit(
            ExecutionRequest(
                execution_id=f"exec_{session_id}",
                session_id=session_id,
                execution_type=ExecutionType.SPEECH,
                priority=TaskPriority.HIGH,
                payload={"text": phone_hint.text},
            )
        )
        self.server.state_log_store.append_task_event(
            session_id=session_id,
            event={
                "event_name": "phone_hint_generated",
                "hint": phone_hint.to_dict(),
            },
        )
        self.server.background_task_center.transition_session(
            session_id=session_id,
            status=TaskStatus.RUNNING,
            phase="guiding",
            summary={"hint_text": phone_hint.text},
        )

        final_status = TaskStatus.RUNNING
        final_phase = "guiding"
        if mark_completed:
            self.phone.local_task_center.finish_session(session_id)
            self.server.state_log_store.append_task_event(
                session_id=session_id,
                event={
                    "event_name": "find_object_completed",
                    "target_name": target_name,
                },
            )
            self.server.background_task_center.transition_session(
                session_id=session_id,
                status=TaskStatus.COMPLETED,
                phase="completed",
                summary={"target_name": target_name, "completed_at": datetime.now().astimezone().isoformat()},
                result={"hint_text": phone_hint.text},
            )
            self.glass.sensor_hub.cancel_capture_request(capture_request.request_id)
            final_status = TaskStatus.COMPLETED
            final_phase = "completed"
        else:
            self.server.state_log_store.append_task_event(
                session_id=session_id,
                event={
                    "event_name": "find_object_scanning",
                    "target_name": target_name,
                    "hint_text": phone_hint.text,
                },
            )

        session = self.server.background_task_center.get_session(session_id)
        if session is None:
            raise RuntimeError("任务实例不存在。")
        snapshot = self.server.state_log_store.get_task_snapshot(session_id)
        if snapshot is None:
            raise RuntimeError("任务日志快照不存在。")

        return FindObjectRunResult(
            session_id=session_id,
            task_name="find_object",
            target_name=target_name,
            voice_event=voice_event.to_dict(),
            task_create_result=dispatch_result,
            capture_grant=capture_grant.to_dict(),
            phone_hint=phone_hint.to_dict(),
            execution_feedback=execution_feedback.to_dict(),
            task_status_snapshot=session.to_dict(),
            task_log_snapshot=snapshot,
            final_status=final_status.value,
            final_phase=final_phase,
            server_recent_logs=self.server.state_log_store.get_recent_records(limit=10),
        )
