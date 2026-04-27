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
class PhoneTaskSnapshot:
    """手机任务快照。"""

    task_id: str
    task_type: str
    state: str
    params: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)
    results: list[dict[str, Any]] = field(default_factory=list)
    frames_processed: int = 0


@dataclass(slots=True)
class _PhoneTaskRecord:
    """手机运行时内部任务记录。"""

    task_id: str
    task_type: str
    context: PhoneTaskContext
    frames_processed: int = 0

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
        )
        self._tasks[task_id] = record
        task.on_start(context)
        return record.to_snapshot()

    def process_task_frame(self, *, task_id: str, frame: Any) -> PhoneTaskSnapshot:
        """向指定手机任务输入一帧数据。"""

        record = self._require_task(task_id)
        task = self._registry.get_phone_task(record.task_type)
        if task is None:
            raise RuntimeError(f"未注册手机任务类型: {record.task_type}")
        task.on_frame(record.context, frame)
        record.frames_processed += 1
        return record.to_snapshot()

    def process_frame(
        self,
        *,
        frame: Any,
        stream_id: str | None = None,
        task_types: list[str] | None = None,
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

        返回值：
        1. 实际收到帧的手机任务快照列表。

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
            task.on_frame(record.context, frame)
            record.frames_processed += 1
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
