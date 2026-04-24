"""backend-task-core 统一任务访问网关。"""

from __future__ import annotations

import threading
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from backend_task_core.event_bus import TaskEventBus
from backend_task_core.models import TaskEvent, TaskRuntime, now_ms
from backend_task_core.registry import TaskRegistry
from backend_task_core.state_machine import TaskStateMachine
from backend_task_core.store import TaskContextStore
from infra.errors import ErrorCode, build_error


def generate_id(prefix: str) -> str:
    """生成统一前缀标识。"""

    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class TaskGateway(ABC):
    """任务网关抽象接口。

    主要功能：
    1. 对上提供任务创建、查询、取消能力。
    2. 对外提供任务事件订阅入口。
    """

    @abstractmethod
    def create_task(
        self,
        *,
        task_type: str,
        session_id: str,
        device_id: str,
        input_data: dict[str, Any],
    ) -> TaskRuntime:
        """创建任务实例。"""

    @abstractmethod
    def query_task(self, task_id: str) -> TaskRuntime:
        """查询任务实例。"""

    @abstractmethod
    def cancel_task(self, task_id: str) -> TaskRuntime:
        """取消任务实例。"""

    @abstractmethod
    def report_find_object_result(
        self,
        *,
        task_id: str,
        found: bool,
        target_object: str,
        confidence: float,
        position: str,
        frame_seq: int | None,
        summary: str,
    ) -> TaskRuntime:
        """上报一次找物体检测结果。"""

    @abstractmethod
    def subscribe_events(self, listener: Callable[[TaskEvent], None]) -> None:
        """订阅任务事件。"""

    @abstractmethod
    def shutdown(self) -> None:
        """关闭任务网关内部后台资源。"""


class InMemoryTaskGateway(TaskGateway):
    """带真实生命周期的内存任务网关。

    主要功能：
    1. 以内存存储承载任务实例，但不再只是静态字典。
    2. 对 `timer_task` 提供真正的创建、倒计时完成、取消与事件发布能力。
    3. 为后续切换更正式的 `TaskManager` 保留稳定 northbound 接口。
    """

    def __init__(self) -> None:
        self._registry = TaskRegistry()
        self._store = TaskContextStore()
        self._state_machine = TaskStateMachine()
        self._event_bus = TaskEventBus()
        self._lock = threading.Lock()
        self._timers: dict[str, threading.Timer] = {}

    def create_task(
        self,
        *,
        task_type: str,
        session_id: str,
        device_id: str,
        input_data: dict[str, Any],
    ) -> TaskRuntime:
        """创建任务实例。

        主要逻辑：
        1. 读取任务模板并校验输入。
        2. 先写入 `scheduled`，再推进到 `running`。
        3. 对计时器任务启动后台定时器，超时后自动完成并发事件。
        """

        spec = self._registry.get_spec(task_type)
        task_id = generate_id("task")
        created_at = now_ms()
        if task_type == "timer_task":
            duration_seconds = self._extract_duration_seconds(input_data)
            runtime = TaskRuntime(
                task_id=task_id,
                task_type=spec.task_type,
                version=spec.version,
                session_id=session_id,
                device_id=device_id,
                state="scheduled",
                input={
                    "duration_seconds": duration_seconds,
                    "label": input_data.get("label"),
                },
                context={
                    "phase": "scheduled",
                    "created_by": "agent_core_phase_f",
                    "scheduled_at_ms": created_at,
                    "duration_seconds": duration_seconds,
                    "label": input_data.get("label"),
                    "deadline_at_ms": created_at + duration_seconds * 1000,
                },
                started_at_ms=created_at,
            )
            runtime = self._store.save(runtime)
            self._publish_runtime_event(
                runtime=runtime,
                event_name="task.created",
                priority="normal",
                requires_agent_decision=False,
                allow_direct_notify=False,
                payload={
                    "message": f"已创建 {duration_seconds} 秒计时器",
                    "duration_seconds": duration_seconds,
                    "label": input_data.get("label"),
                },
            )

            runtime = self._transition_runtime(
                runtime=runtime,
                to_state="running",
                phase="counting_down",
            )
            self._publish_runtime_event(
                runtime=runtime,
                event_name="task.started",
                priority="normal",
                requires_agent_decision=False,
                allow_direct_notify=False,
                payload={
                    "message": f"计时器已启动，倒计时 {duration_seconds} 秒",
                    "duration_seconds": duration_seconds,
                    "label": input_data.get("label"),
                },
            )
            self._schedule_timer_completion(runtime.task_id, duration_seconds)
            return runtime

        if task_type == "phone_video_link_task":
            phone_device_id = self._extract_phone_device_id(input_data)
            target_ws_uri = self._extract_target_ws_uri(input_data)
            link_mode = str(input_data.get("link_mode") or "direct").strip() or "direct"
            reason = str(input_data.get("reason") or "agent_requested").strip() or "agent_requested"
            frame_interval_ms = self._extract_frame_interval_ms(input_data)
            stream_id = str(input_data.get("stream_id") or generate_id("stream")).strip()
            runtime = TaskRuntime(
                task_id=task_id,
                task_type=spec.task_type,
                version=spec.version,
                session_id=session_id,
                device_id=device_id,
                state="scheduled",
                input={
                    "phone_device_id": phone_device_id,
                    "target_ws_uri": target_ws_uri,
                    "link_mode": link_mode,
                    "reason": reason,
                    "frame_interval_ms": frame_interval_ms,
                    "stream_id": stream_id,
                },
                context={
                    "phase": "scheduled",
                    "created_by": "agent_core_phase_j",
                    "glass_device_id": device_id,
                    "phone_device_id": phone_device_id,
                    "target_ws_uri": target_ws_uri,
                    "link_mode": link_mode,
                    "reason": reason,
                    "frame_interval_ms": frame_interval_ms,
                    "stream_id": stream_id,
                },
                started_at_ms=created_at,
            )
            runtime = self._store.save(runtime)
            self._publish_runtime_event(
                runtime=runtime,
                event_name="task.created",
                priority="normal",
                requires_agent_decision=False,
                allow_direct_notify=False,
                payload={
                    "message": f"已创建眼镜与手机视频直连任务，目标手机是 {phone_device_id}",
                    "phone_device_id": phone_device_id,
                    "target_ws_uri": target_ws_uri,
                    "link_mode": link_mode,
                    "reason": reason,
                    "frame_interval_ms": frame_interval_ms,
                    "stream_id": stream_id,
                },
            )
            runtime = self._transition_runtime(
                runtime=runtime,
                to_state="running",
                phase="link_prepared",
            )
            self._publish_runtime_event(
                runtime=runtime,
                event_name="task.started",
                priority="normal",
                requires_agent_decision=False,
                allow_direct_notify=False,
                payload={
                    "message": f"视频直连任务已进入运行态，目标手机是 {phone_device_id}",
                    "phone_device_id": phone_device_id,
                    "target_ws_uri": target_ws_uri,
                    "link_mode": link_mode,
                    "reason": reason,
                    "frame_interval_ms": frame_interval_ms,
                    "codec": "jpeg",
                    "stream_id": stream_id,
                },
            )
            return runtime

        if task_type == "find_object_task":
            target_object = self._extract_target_object(input_data)
            phone_device_id = self._extract_phone_device_id(input_data)
            target_ws_uri = self._extract_target_ws_uri(input_data)
            frame_interval_ms = self._extract_frame_interval_ms(input_data)
            stream_id = str(input_data.get("stream_id") or generate_id("stream")).strip()
            runtime = TaskRuntime(
                task_id=task_id,
                task_type=spec.task_type,
                version=spec.version,
                session_id=session_id,
                device_id=device_id,
                state="scheduled",
                input={
                    "target_object": target_object,
                    "phone_device_id": phone_device_id,
                    "target_ws_uri": target_ws_uri,
                    "frame_interval_ms": frame_interval_ms,
                    "stream_id": stream_id,
                    "reason": str(input_data.get("reason") or "agent_requested").strip() or "agent_requested",
                },
                context={
                    "phase": "scheduled",
                    "created_by": "agent_core_phase_k",
                    "glass_device_id": device_id,
                    "phone_device_id": phone_device_id,
                    "target_object": target_object,
                    "target_ws_uri": target_ws_uri,
                    "frame_interval_ms": frame_interval_ms,
                    "stream_id": stream_id,
                    "latest_detection": None,
                },
                started_at_ms=created_at,
            )
            runtime = self._store.save(runtime)
            self._publish_runtime_event(
                runtime=runtime,
                event_name="task.created",
                priority="normal",
                requires_agent_decision=False,
                allow_direct_notify=False,
                payload={
                    "message": f"已创建找物体任务，目标是 {target_object}",
                    "target_object": target_object,
                    "phone_device_id": phone_device_id,
                    "target_ws_uri": target_ws_uri,
                    "frame_interval_ms": frame_interval_ms,
                    "stream_id": stream_id,
                },
            )
            runtime = self._transition_runtime(
                runtime=runtime,
                to_state="running",
                phase="detecting",
            )
            self._publish_runtime_event(
                runtime=runtime,
                event_name="task.started",
                priority="normal",
                requires_agent_decision=False,
                allow_direct_notify=False,
                payload={
                    "message": f"开始在手机端寻找 {target_object}",
                    "target_object": target_object,
                    "phone_device_id": phone_device_id,
                    "target_ws_uri": target_ws_uri,
                    "frame_interval_ms": frame_interval_ms,
                    "codec": "jpeg",
                    "stream_id": stream_id,
                },
            )
            return runtime

        if task_type == "navigation_task":
            destination = self._extract_destination(input_data)
            origin = str(input_data.get("origin") or "当前位置").strip() or "当前位置"
            route_summary = str(input_data.get("route_summary") or "").strip()
            phone_device_id = str(input_data.get("phone_device_id") or "mock-phone").strip() or "mock-phone"
            runtime = TaskRuntime(
                task_id=task_id,
                task_type=spec.task_type,
                version=spec.version,
                session_id=session_id,
                device_id=device_id,
                state="scheduled",
                input={
                    "destination": destination,
                    "origin": origin,
                    "route_summary": route_summary,
                    "phone_device_id": phone_device_id,
                    "phone_runtime": "mock",
                },
                context={
                    "phase": "scheduled",
                    "created_by": "agent_core_skill_navigation_guide",
                    "glass_device_id": device_id,
                    "phone_device_id": phone_device_id,
                    "destination": destination,
                    "origin": origin,
                    "route_summary": route_summary,
                    "phone_runtime": "mock",
                },
                started_at_ms=created_at,
            )
            runtime = self._store.save(runtime)
            self._publish_runtime_event(
                runtime=runtime,
                event_name="task.created",
                priority="normal",
                requires_agent_decision=False,
                allow_direct_notify=False,
                payload={
                    "message": f"已创建前往 {destination} 的模拟导航任务",
                    "destination": destination,
                    "origin": origin,
                    "phone_device_id": phone_device_id,
                    "phone_runtime": "mock",
                },
            )
            runtime = self._transition_runtime(
                runtime=runtime,
                to_state="running",
                phase="mock_navigating",
            )
            self._publish_runtime_event(
                runtime=runtime,
                event_name="task.started",
                priority="normal",
                requires_agent_decision=False,
                allow_direct_notify=False,
                payload={
                    "message": f"模拟导航已启动，目的地是 {destination}",
                    "destination": destination,
                    "origin": origin,
                    "phone_device_id": phone_device_id,
                    "phone_runtime": "mock",
                },
            )
            return runtime

        raise build_error(
            ErrorCode.TASK_NOT_FOUND,
            "当前不支持指定任务类型",
            details={"task_type": task_type},
        )

    def query_task(self, task_id: str) -> TaskRuntime:
        """查询任务实例。"""

        runtime = self._store.get(task_id)
        if runtime is None:
            raise build_error(
                ErrorCode.TASK_NOT_FOUND,
                "目标任务不存在",
                details={"task_id": task_id},
            )
        runtime.updated_at_ms = now_ms()
        return self._store.update(runtime)

    def cancel_task(self, task_id: str) -> TaskRuntime:
        """取消任务实例。

        主要逻辑：
        1. 查询目标任务。
        2. 若仍处于活动态，则停止后台定时器并推进到 `cancelled`。
        3. 发布终态事件。
        """

        runtime = self.query_task(task_id)
        if runtime.state in {"failed", "timeout", "cancelled", "completed"}:
            return runtime

        if runtime.task_type == "timer_task":
            self._cancel_timer_handle(task_id)
        cancel_message = "任务已取消"
        cancel_payload: dict[str, Any] = {"message": "计时器已取消"}
        requires_agent_decision = True
        allow_direct_notify = True
        if runtime.task_type == "phone_video_link_task":
            cancel_message = "视频直连任务已取消"
            cancel_payload = {
                "message": cancel_message,
                "phone_device_id": runtime.input.get("phone_device_id"),
                "target_ws_uri": runtime.input.get("target_ws_uri"),
                "stream_id": runtime.input.get("stream_id"),
            }
            requires_agent_decision = False
            allow_direct_notify = False
        if runtime.task_type == "find_object_task":
            cancel_message = "找物体任务已取消"
            cancel_payload = {
                "message": cancel_message,
                "target_object": runtime.input.get("target_object"),
                "phone_device_id": runtime.input.get("phone_device_id"),
                "target_ws_uri": runtime.input.get("target_ws_uri"),
                "stream_id": runtime.input.get("stream_id"),
            }
            requires_agent_decision = False
            allow_direct_notify = False
        if runtime.task_type == "navigation_task":
            cancel_message = "模拟导航任务已取消"
            cancel_payload = {
                "message": cancel_message,
                "destination": runtime.input.get("destination"),
                "phone_device_id": runtime.input.get("phone_device_id"),
                "phone_runtime": runtime.input.get("phone_runtime"),
            }
            requires_agent_decision = False
            allow_direct_notify = False

        runtime = self._transition_runtime(
            runtime=runtime,
            to_state="cancelled",
            phase="cancelled",
            result={"message": cancel_message},
        )
        self._publish_runtime_event(
            runtime=runtime,
            event_name="task.cancelled",
            priority="normal",
            requires_agent_decision=requires_agent_decision,
            allow_direct_notify=allow_direct_notify,
            payload=cancel_payload,
        )
        return runtime

    def report_find_object_result(
        self,
        *,
        task_id: str,
        found: bool,
        target_object: str,
        confidence: float,
        position: str,
        frame_seq: int | None,
        summary: str,
    ) -> TaskRuntime:
        """上报手机端找物体检测结果。

        主要逻辑：
        1. 查询并校验目标任务仍在运行。
        2. 保存最近一次结构化检测结果。
        3. 未找到目标时发布 `task.updated`。
        4. 找到目标时推进到 `completed` 并发布可播报的 `task.completed`。
        """

        runtime = self.query_task(task_id)
        if runtime.task_type != "find_object_task":
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "目标任务不是 find_object_task",
                details={"task_id": task_id, "task_type": runtime.task_type},
            )
        if runtime.state != "running":
            return runtime

        normalized_target = target_object.strip() or str(runtime.input.get("target_object") or "")
        detection = {
            "found": bool(found),
            "target_object": normalized_target,
            "phone_device_id": runtime.input.get("phone_device_id"),
            "target_ws_uri": runtime.input.get("target_ws_uri"),
            "stream_id": runtime.input.get("stream_id"),
            "confidence": max(0.0, min(float(confidence), 1.0)),
            "position": position.strip() or "unknown",
            "frame_seq": frame_seq,
            "summary": summary.strip() or ("找到目标" if found else "暂未找到目标"),
            "reported_at_ms": now_ms(),
        }
        runtime.context["latest_detection"] = detection
        runtime.updated_at_ms = now_ms()

        if not found:
            runtime = self._store.update(runtime)
            self._publish_runtime_event(
                runtime=runtime,
                event_name="task.updated",
                priority="low",
                requires_agent_decision=False,
                allow_direct_notify=False,
                payload=detection,
            )
            return runtime

        runtime = self._transition_runtime(
            runtime=runtime,
            to_state="completed",
            phase="completed",
            result=detection,
        )
        message = detection["summary"] or f"找到{normalized_target}了"
        self._publish_runtime_event(
            runtime=runtime,
            event_name="task.completed",
            priority="high",
            requires_agent_decision=True,
            allow_direct_notify=True,
            payload={
                **detection,
                "message": message,
            },
        )
        return runtime

    def subscribe_events(self, listener: Callable[[TaskEvent], None]) -> None:
        """订阅任务事件。"""

        self._event_bus.subscribe(listener)

    def shutdown(self) -> None:
        """关闭任务网关。

        主要逻辑：
        1. 取消所有尚未完成的后台定时器。
        2. 避免测试或服务关闭后残留线程继续运行。
        """

        with self._lock:
            timers = list(self._timers.values())
            self._timers.clear()
        for timer in timers:
            timer.cancel()

    def _extract_duration_seconds(self, input_data: dict[str, Any]) -> int:
        """提取并校验计时秒数。"""

        try:
            duration_seconds = int(input_data.get("duration_seconds", 0))
        except (TypeError, ValueError) as exc:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "创建计时器需要合法的 duration_seconds",
                details={"input_data": input_data},
            ) from exc
        if duration_seconds <= 0:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "创建计时器需要 duration_seconds 大于 0",
                details={"input_data": input_data},
            )
        return duration_seconds

    def _extract_phone_device_id(self, input_data: dict[str, Any]) -> str:
        """提取并校验目标手机编号。"""

        phone_device_id = str(input_data.get("phone_device_id", "")).strip()
        if not phone_device_id:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "创建视频直连任务需要 phone_device_id",
                details={"input_data": input_data},
            )
        return phone_device_id

    def _extract_target_ws_uri(self, input_data: dict[str, Any]) -> str:
        """提取并校验目标视频接收地址。"""

        target_ws_uri = str(input_data.get("target_ws_uri", "")).strip()
        if not target_ws_uri:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "创建视频直连任务需要 target_ws_uri",
                details={"input_data": input_data},
            )
        return target_ws_uri

    def _extract_frame_interval_ms(self, input_data: dict[str, Any]) -> int:
        """提取帧间隔。"""

        try:
            frame_interval_ms = int(input_data.get("frame_interval_ms", 500))
        except (TypeError, ValueError) as exc:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "frame_interval_ms 必须是整数",
                details={"input_data": input_data},
            ) from exc
        if frame_interval_ms <= 0:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "frame_interval_ms 必须大于 0",
                details={"frame_interval_ms": frame_interval_ms},
            )
        return frame_interval_ms

    def _extract_target_object(self, input_data: dict[str, Any]) -> str:
        """提取并校验找物体目标名称。"""

        target_object = str(input_data.get("target_object", "")).strip()
        if not target_object:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "创建找物体任务需要 target_object",
                details={"input_data": input_data},
            )
        return target_object

    def _extract_destination(self, input_data: dict[str, Any]) -> str:
        """提取并校验导航目的地。"""

        destination = str(input_data.get("destination", "")).strip()
        if not destination:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "创建导航任务需要 destination",
                details={"input_data": input_data},
            )
        return destination

    def _transition_runtime(
        self,
        *,
        runtime: TaskRuntime,
        to_state: str,
        phase: str,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> TaskRuntime:
        """推进任务状态并回写存储。"""

        self._state_machine.ensure_transition(from_state=runtime.state, to_state=to_state)
        runtime.state = to_state
        runtime.updated_at_ms = now_ms()
        runtime.context["phase"] = phase
        if result is not None:
            runtime.result = dict(result)
        if error is not None:
            runtime.error = dict(error)
        if to_state in {"completed", "cancelled", "failed", "timeout"}:
            runtime.completed_at_ms = runtime.updated_at_ms
        return self._store.update(runtime)

    def _schedule_timer_completion(self, task_id: str, duration_seconds: int) -> None:
        """启动计时器完成调度。"""

        timer = threading.Timer(duration_seconds, self._complete_timer_task, args=(task_id,))
        timer.daemon = True
        with self._lock:
            self._timers[task_id] = timer
        timer.start()

    def _cancel_timer_handle(self, task_id: str) -> None:
        """取消底层定时器句柄。"""

        with self._lock:
            timer = self._timers.pop(task_id, None)
        if timer is not None:
            timer.cancel()

    def _complete_timer_task(self, task_id: str) -> None:
        """处理计时器自然完成。

        主要逻辑：
        1. 若任务已被取消或已完成，则直接返回。
        2. 把任务推进到 `completed`。
        3. 发布 `task.completed` 事件。
        """

        self._cancel_timer_handle(task_id)
        runtime = self._store.get(task_id)
        if runtime is None or runtime.state != "running":
            return
        runtime = self._transition_runtime(
            runtime=runtime,
            to_state="completed",
            phase="completed",
            result={"message": "计时结束"},
        )
        self._publish_runtime_event(
            runtime=runtime,
            event_name="task.completed",
            priority="high",
            requires_agent_decision=True,
            allow_direct_notify=True,
            payload={
                "message": "计时结束了",
                "duration_seconds": runtime.input.get("duration_seconds"),
                "label": runtime.input.get("label"),
            },
        )

    def _publish_runtime_event(
        self,
        *,
        runtime: TaskRuntime,
        event_name: str,
        priority: str,
        requires_agent_decision: bool,
        allow_direct_notify: bool,
        payload: dict[str, Any],
    ) -> None:
        """发布一条任务事件。"""

        event = TaskEvent(
            event_id=generate_id("evt"),
            event_name=event_name,
            task_id=runtime.task_id,
            task_type=runtime.task_type,
            session_id=runtime.session_id,
            device_id=runtime.device_id,
            state=runtime.state,
            priority=priority,
            requires_agent_decision=requires_agent_decision,
            allow_direct_notify=allow_direct_notify,
            ts=now_ms(),
            payload=dict(payload),
        )
        self._event_bus.publish(event)
