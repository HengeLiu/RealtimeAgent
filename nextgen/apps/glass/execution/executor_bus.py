"""执行总线实现。"""

from copy import deepcopy
from datetime import datetime
from typing import List, Optional

from nextgen.shared.contracts.execution import ExecutorBus
from nextgen.shared.models.execution import ExecutionFeedback, ExecutionRequest


class GlassExecutorBus(ExecutorBus):
    """眼镜端执行总线。

    主要功能：
    - 接收执行请求并做最小调度
    - 支持忙碌时排队、丢弃或抢占
    - 不真正播放音频或控制震动，只维护执行状态
    """

    def __init__(self) -> None:
        """初始化执行总线。"""

        self.queue: List[ExecutionRequest] = []
        self.current_request: Optional[ExecutionRequest] = None

    def submit(self, request: ExecutionRequest) -> ExecutionFeedback:
        """提交执行请求。

        参数：
        - request：执行请求对象。

        返回值：
        - 当前阶段直接返回 `queued` 状态的反馈。
        """

        status = "queued"
        if self.current_request is None:
            self.current_request = deepcopy(request)
            status = "running"
        elif request.policy.drop_if_busy:
            status = "dropped"
        elif (
            self.current_request.policy.interruptible
            and request.priority.value in {"urgent", "high"}
            and request.priority.value != self.current_request.priority.value
        ):
            self.queue.insert(0, deepcopy(self.current_request))
            self.current_request = deepcopy(request)
            status = "preempted_running"
        else:
            self.queue.append(deepcopy(request))

        return ExecutionFeedback(
            execution_id=request.execution_id,
            status=status,
            timestamp=datetime.now().astimezone().isoformat(),
        )

    def complete_current(self) -> Optional[ExecutionRequest]:
        """完成当前执行请求，并取出下一条。"""

        finished = self.current_request
        self.current_request = self.queue.pop(0) if self.queue else None
        return finished

    def get_state_snapshot(self) -> dict:
        """获取当前执行状态快照。"""

        return {
            "current_request": self.current_request.to_dict() if self.current_request else None,
            "queue_size": len(self.queue),
        }
