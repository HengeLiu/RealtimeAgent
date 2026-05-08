"""手机侧运行时。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from openaiglasses.phone.base_phone_task import PhoneTaskContext
from openaiglasses.phone.base_processor import PhoneProcessorContext
from openaiglasses.phone.sensor_provider import SensorReading

if TYPE_CHECKING:
    from openaiglasses.capabilities.registry import CapabilityRegistry


def _new_phone_task_id() -> str:
    """生成手机任务编号。"""

    return f"phone_task_{uuid.uuid4().hex[:12]}"


@dataclass(slots=True)
class VisionTaskPolicy:
    """手机视觉任务资源策略。

    主要功能：
    1. 描述单个手机视觉任务在 SDK 运行时中的帧处理限制。
    2. 让 SDK 在业务任务之外统一做帧率限制和过载记录。
    3. 为后续手机端模型资源、并发和优先级调度预留稳定字段。

    主要属性：
    1. `min_frame_interval_ms`：同一任务两次实际处理帧之间的最小间隔。
    2. `max_frames`：单个任务最多允许处理的帧数，`None` 表示不限制。
    3. `priority`：任务优先级，当前仅记录在策略中，后续用于多任务资源仲裁。
    4. `emit_overload_events`：触发限流时是否记录结构化过载事件。
    """

    min_frame_interval_ms: int = 0
    max_frames: int | None = None
    priority: int = 0
    emit_overload_events: bool = True

    @classmethod
    def from_params(cls, params: dict[str, Any] | None) -> "VisionTaskPolicy":
        """从任务参数中读取视觉资源策略。

        参数：
        1. `params`：手机任务启动参数。

        返回值：
        1. 解析后的视觉任务资源策略。

        异常情况：
        1. 参数类型异常时会按默认值降级，不主动抛出异常。
        """

        raw_params = dict(params or {})
        raw_policy = raw_params.get("vision_policy")
        policy_data = dict(raw_policy) if isinstance(raw_policy, dict) else {}
        min_frame_interval_ms = cls._read_int(
            policy_data,
            raw_params,
            keys=("min_frame_interval_ms", "frame_interval_ms"),
            default=0,
            minimum=0,
        )
        max_frames_value = cls._read_int(
            policy_data,
            raw_params,
            keys=("max_frames",),
            default=0,
            minimum=0,
        )
        priority = cls._read_int(
            policy_data,
            raw_params,
            keys=("priority",),
            default=0,
            minimum=0,
        )
        emit_overload_events = policy_data.get(
            "emit_overload_events",
            raw_params.get("emit_overload_events", True),
        )
        return cls(
            min_frame_interval_ms=min_frame_interval_ms,
            max_frames=max_frames_value or None,
            priority=priority,
            emit_overload_events=bool(emit_overload_events),
        )

    def to_dict(self) -> dict[str, Any]:
        """导出可序列化策略。

        返回值：
        1. 可写入快照和日志的策略字典。

        异常情况：
        1. 本函数不主动抛出异常。
        """

        return {
            "min_frame_interval_ms": self.min_frame_interval_ms,
            "max_frames": self.max_frames,
            "priority": self.priority,
            "emit_overload_events": self.emit_overload_events,
        }

    @staticmethod
    def _read_int(
        *sources: dict[str, Any],
        keys: tuple[str, ...],
        default: int,
        minimum: int,
    ) -> int:
        """从多组参数中读取整数配置。

        参数：
        1. `sources`：按优先级排列的配置字典。
        2. `keys`：允许读取的字段名。
        3. `default`：读取失败时使用的默认值。
        4. `minimum`：允许的最小值。

        返回值：
        1. 解析后的整数。

        异常情况：
        1. 非整数内容会被忽略。
        """

        for source in sources:
            for key in keys:
                if key not in source:
                    continue
                try:
                    return max(minimum, int(source[key]))
                except (TypeError, ValueError):
                    return default
        return default


@dataclass(slots=True)
class PhoneTaskSnapshot:
    """手机任务快照。"""

    task_id: str
    task_type: str
    state: str
    params: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)
    results: list[dict[str, Any]] = field(default_factory=list)
    frames_processed: int = 0
    frames_dropped: int = 0
    resource_events: list[dict[str, Any]] = field(default_factory=list)
    vision_policy: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _PhoneTaskRecord:
    """手机运行时内部任务记录。"""

    task_id: str
    task_type: str
    context: PhoneTaskContext
    vision_policy: VisionTaskPolicy = field(default_factory=VisionTaskPolicy)
    frames_processed: int = 0
    frames_dropped: int = 0
    last_dispatched_at_ms: int | None = None
    resource_events: list[dict[str, Any]] = field(default_factory=list)

    def to_snapshot(self) -> PhoneTaskSnapshot:
        """导出手机任务快照。"""

        return PhoneTaskSnapshot(
            task_id=self.task_id,
            task_type=self.task_type,
            state=self.context.state,
            params=dict(self.context.params),
            data=dict(self.context.data),
            results=[dict(item) for item in self.context.results],
            frames_processed=self.frames_processed,
            frames_dropped=self.frames_dropped,
            resource_events=[dict(item) for item in self.resource_events],
            vision_policy=self.vision_policy.to_dict(),
        )


class PhoneRuntime:
    """手机侧最小运行时。

    主要功能：
    1. 托管手机任务、手机处理器和传感器提供者。
    2. 为样例回放和后续手机端宿主提供统一调度入口。
    """

    def __init__(self, *, registry: "CapabilityRegistry") -> None:
        self._registry = registry
        self._tasks: dict[str, _PhoneTaskRecord] = {}

    def start_task(
        self,
        *,
        task_type: str,
        params: dict[str, Any] | None = None,
    ) -> PhoneTaskSnapshot:
        """启动一个手机任务。"""

        task = self._registry.get_phone_task(task_type)
        if task is None:
            raise RuntimeError(f"未注册手机任务类型: {task_type}")
        task_id = _new_phone_task_id()
        context = PhoneTaskContext(
            runtime=self,
            task_id=task_id,
            params=dict(params or {}),
        )
        record = _PhoneTaskRecord(
            task_id=task_id,
            task_type=task_type,
            context=context,
            vision_policy=VisionTaskPolicy.from_params(params),
        )
        self._tasks[task_id] = record
        task.on_start(context)
        return record.to_snapshot()

    def process_task_frame(
        self,
        *,
        task_id: str,
        frame: Any,
        now_ms: int | None = None,
    ) -> PhoneTaskSnapshot:
        """向指定手机任务输入一帧数据。"""

        record = self._require_task(task_id)
        task = self._registry.get_phone_task(record.task_type)
        if task is None:
            raise RuntimeError(f"未注册手机任务类型: {record.task_type}")
        self._dispatch_frame_to_record(
            record=record,
            task=task,
            frame=frame,
            stream_id=None,
            now_ms=now_ms,
        )
        return record.to_snapshot()

    def process_frame(
        self,
        *,
        frame: Any,
        stream_id: str | None = None,
        task_types: list[str] | None = None,
        now_ms: int | None = None,
    ) -> list[PhoneTaskSnapshot]:
        """把一帧输入分发给匹配的活跃手机任务。

        主要逻辑：
        1. 默认向全部非终态手机任务广播当前帧。
        2. 若任务参数中声明了 `stream_id`，只接收同一路视频流的帧。
        3. 若调用方传入 `task_types`，只分发给指定任务类型。

        参数：
        1. `frame`：端侧收到的视频帧或传感器帧对象。
        2. `stream_id`：当前帧所属视频流编号，可为空。
        3. `task_types`：可选任务类型白名单。
        4. `now_ms`：可选的当前时间戳，测试和回放可传入固定值。

        返回值：
        1. 匹配当前帧的手机任务快照列表。若某个任务因资源策略丢帧，也会返回带资源事件的快照。

        异常情况：
        1. 若某个匹配任务类型未注册，抛出 `RuntimeError`。
        """

        allowed_types = {str(item) for item in task_types or []}
        snapshots: list[PhoneTaskSnapshot] = []
        for record in list(self._tasks.values()):
            if allowed_types and record.task_type not in allowed_types:
                continue
            if not self._should_route_frame(record=record, stream_id=stream_id):
                continue
            task = self._registry.get_phone_task(record.task_type)
            if task is None:
                raise RuntimeError(f"未注册手机任务类型: {record.task_type}")
            self._dispatch_frame_to_record(
                record=record,
                task=task,
                frame=frame,
                stream_id=stream_id,
                now_ms=now_ms,
            )
            snapshots.append(record.to_snapshot())
        return snapshots

    def stop_task(self, task_id: str) -> PhoneTaskSnapshot:
        """停止一个手机任务。"""

        record = self._require_task(task_id)
        task = self._registry.get_phone_task(record.task_type)
        if task is None:
            raise RuntimeError(f"未注册手机任务类型: {record.task_type}")
        task.on_stop(record.context)
        return record.to_snapshot()

    def query_task(self, task_id: str) -> PhoneTaskSnapshot:
        """查询手机任务快照。"""

        return self._require_task(task_id).to_snapshot()

    def list_tasks(self) -> list[PhoneTaskSnapshot]:
        """列出当前全部手机任务快照。

        返回值：
        1. 当前运行时托管的全部手机任务快照列表。

        异常情况：
        1. 本函数不主动抛出异常。
        """

        return [record.to_snapshot() for record in self._tasks.values()]

    def process_with_processor(
        self,
        *,
        processor_type: str,
        frame: Any,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """通过手机处理器处理输入。"""

        processor = self._registry.get_phone_processor(processor_type)
        if processor is None:
            raise RuntimeError(f"未注册手机处理器类型: {processor_type}")
        context = PhoneProcessorContext(params=dict(params or {}))
        processor.on_frame(context, frame)
        if not context.results:
            return {}
        return dict(context.results[-1])

    def read_sensor(self, sensor_type: str) -> SensorReading:
        """读取一次传感器数据。"""

        provider = self._registry.get_sensor_provider(sensor_type)
        if provider is None:
            raise RuntimeError(f"未注册传感器类型: {sensor_type}")
        return provider.read()

    def _require_task(self, task_id: str) -> _PhoneTaskRecord:
        """读取手机任务记录。"""

        record = self._tasks.get(task_id)
        if record is None:
            raise RuntimeError(f"手机任务不存在: {task_id}")
        return record

    @staticmethod
    def _should_route_frame(*, record: _PhoneTaskRecord, stream_id: str | None) -> bool:
        """判断当前帧是否应分发给指定手机任务。"""

        if record.context.state in {"completed", "cancelled", "failed", "stopped"}:
            return False
        expected_stream_id = str(record.context.params.get("stream_id") or "").strip()
        if expected_stream_id and expected_stream_id != str(stream_id or "").strip():
            return False
        return True

    def _dispatch_frame_to_record(
        self,
        *,
        record: _PhoneTaskRecord,
        task: Any,
        frame: Any,
        stream_id: str | None,
        now_ms: int | None,
    ) -> bool:
        """按资源策略向单个手机任务分发帧。

        参数：
        1. `record`：手机任务运行时记录。
        2. `task`：开发者注册的手机任务对象。
        3. `frame`：当前输入帧。
        4. `stream_id`：当前输入帧所属视频流。
        5. `now_ms`：当前时间戳，测试可传入固定值。

        返回值：
        1. `True` 表示帧已交给任务处理。
        2. `False` 表示帧因资源策略被丢弃。

        异常情况：
        1. 任务处理帧时抛出的异常会继续向上抛出，由调用方或测试暴露问题。
        """

        frame_ts_ms = self._extract_frame_ts_ms(frame=frame, now_ms=now_ms)
        overload_reason = self._check_overload(record=record, frame_ts_ms=frame_ts_ms)
        if overload_reason:
            self._record_overload_event(
                record=record,
                reason=overload_reason,
                stream_id=stream_id,
                frame_ts_ms=frame_ts_ms,
            )
            return False
        task.on_frame(record.context, frame)
        record.frames_processed += 1
        if frame_ts_ms is not None:
            record.last_dispatched_at_ms = frame_ts_ms
        return True

    @staticmethod
    def _extract_frame_ts_ms(*, frame: Any, now_ms: int | None) -> int | None:
        """从输入帧中读取时间戳。

        参数：
        1. `frame`：当前输入帧，可以是字典或带 `header` 属性的对象。
        2. `now_ms`：调用方显式传入的当前时间戳。

        返回值：
        1. 解析到的毫秒时间戳；无法解析时返回 `None`。

        异常情况：
        1. 非法时间戳会被忽略，不主动抛出异常。
        """

        if now_ms is not None:
            try:
                return int(now_ms)
            except (TypeError, ValueError):
                return None
        if isinstance(frame, dict):
            raw_value = frame.get("ts_ms")
            if raw_value is None and isinstance(frame.get("header"), dict):
                raw_value = frame["header"].get("ts_ms")
            return PhoneRuntime._coerce_optional_int(raw_value)
        raw_value = getattr(frame, "ts_ms", None)
        if raw_value is None:
            header = getattr(frame, "header", None)
            if isinstance(header, dict):
                raw_value = header.get("ts_ms")
        return PhoneRuntime._coerce_optional_int(raw_value)

    @staticmethod
    def _coerce_optional_int(value: Any) -> int | None:
        """把输入转换为可选整数。"""

        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _check_overload(*, record: _PhoneTaskRecord, frame_ts_ms: int | None) -> str | None:
        """检查当前帧是否触发资源限制。"""

        policy = record.vision_policy
        if policy.max_frames is not None and record.frames_processed >= policy.max_frames:
            return "max_frames_reached"
        if (
            policy.min_frame_interval_ms > 0
            and frame_ts_ms is not None
            and record.last_dispatched_at_ms is not None
            and frame_ts_ms - record.last_dispatched_at_ms < policy.min_frame_interval_ms
        ):
            return "frame_rate_limited"
        return None

    @staticmethod
    def _record_overload_event(
        *,
        record: _PhoneTaskRecord,
        reason: str,
        stream_id: str | None,
        frame_ts_ms: int | None,
    ) -> None:
        """记录手机视觉任务过载事件。"""

        record.frames_dropped += 1
        if not record.vision_policy.emit_overload_events:
            return
        event = {
            "event_name": "vision.task.overloaded",
            "reason": reason,
            "task_id": record.task_id,
            "task_type": record.task_type,
            "stream_id": stream_id,
            "frame_ts_ms": frame_ts_ms,
            "frames_processed": record.frames_processed,
            "frames_dropped": record.frames_dropped,
            "min_frame_interval_ms": record.vision_policy.min_frame_interval_ms,
            "max_frames": record.vision_policy.max_frames,
        }
        record.resource_events.append(event)
        record.context.update(
            {
                "latest_resource_event": event,
                "frames_dropped": record.frames_dropped,
            }
        )
