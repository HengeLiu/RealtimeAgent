"""状态与日志存储骨架实现。"""


class StateLogStore:
    """状态与日志存储。

    主要功能：
    - 接收并保存任务或设备状态日志。

    当前阶段：
    - 使用内存列表做最小占位。
    """

    def __init__(self) -> None:
        """初始化状态存储。"""

        self.records: list[dict] = []

    def append(self, record: dict) -> None:
        """追加日志记录。

        参数：
        - record：日志记录对象。
        """

        self.records.append(record)
