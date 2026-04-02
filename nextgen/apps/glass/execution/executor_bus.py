"""执行总线骨架实现。"""

from datetime import datetime

from nextgen.shared.contracts.execution import ExecutorBus
from nextgen.shared.models.execution import ExecutionFeedback, ExecutionRequest


class GlassExecutorBus(ExecutorBus):
    """眼镜端执行总线。

    主要功能：
    - 接收执行请求并返回最小执行反馈。
    - 当前阶段不真正播放音频或控制震动，只做占位。
    """

    def submit(self, request: ExecutionRequest) -> ExecutionFeedback:
        """提交执行请求。

        参数：
        - request：执行请求对象。

        返回值：
        - 当前阶段直接返回 `queued` 状态的反馈。
        """

        return ExecutionFeedback(
            execution_id=request.execution_id,
            status="queued",
            timestamp=datetime.now().astimezone().isoformat(),
        )
