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


@dataclass(slots=True)
class _PhoneTaskRecord:
    """手机运行时内部任务记录。"""

    task_id: str
    task_type: str
    context: PhoneTaskContext

    def to_snapshot(self) -> PhoneTaskSnapshot:
        """导出手机任务快照。"""

        return PhoneTaskSnapshot(
            task_id=self.task_id,
            task_type=self.task_type,
            state=self.context.state,
            params=dict(self.context.params),
            data=dict(self.context.data),
            results=[dict(item) for item in self.context.results],
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
        return record.to_snapshot()

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
