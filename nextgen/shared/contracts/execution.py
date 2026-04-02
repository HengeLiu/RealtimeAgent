"""执行相关抽象接口。"""

from abc import ABC, abstractmethod

from nextgen.shared.models.execution import ExecutionFeedback, ExecutionRequest


class ExecutorBus(ABC):
    """执行总线抽象接口。

    主要功能：
    - 接收执行请求
    - 调度播报、提示音、震动
    - 返回执行反馈
    """

    @abstractmethod
    def submit(self, request: ExecutionRequest) -> ExecutionFeedback:
        """提交执行请求。

        参数：
        - request：执行请求对象。

        返回值：
        - 执行反馈对象。
        """


class DeviceControl(ABC):
    """执行器控制抽象接口。

    主要功能：
    - 管理喇叭和振动器等执行器的基础设置。
    """

    @abstractmethod
    def configure(self, settings: dict) -> None:
        """应用执行器设置。

        参数：
        - settings：执行器相关配置。
        """
