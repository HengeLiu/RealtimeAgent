"""状态与日志存储实现。"""

from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List, Optional


class StateLogStore:
    """状态与日志存储。

    主要功能：
    - 接收并保存任务或设备状态日志
    - 维护任务和设备的最近状态快照
    - 为服务器运行时提供简单可查询的内存态存储

    主要属性：
    - records：原始日志记录列表
    - task_snapshots：任务最近状态快照
    - device_snapshots：设备最近状态快照
    """

    def __init__(self) -> None:
        """初始化状态存储。"""

        self.records: List[Dict[str, Any]] = []
        self.task_snapshots: Dict[str, Dict[str, Any]] = {}
        self.device_snapshots: Dict[str, Dict[str, Any]] = {}

    def append(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """追加日志记录。

        主要逻辑：
        - 若记录中没有时间戳，则自动补当前时间
        - 统一以深拷贝形式保存，避免外部修改影响内部状态

        参数：
        - record：日志记录对象。

        返回值：
        - 保存后的日志记录。
        """

        stored = deepcopy(record)
        stored.setdefault("timestamp", datetime.now().astimezone().isoformat())
        self.records.append(stored)
        return stored

    def append_task_event(self, session_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
        """追加任务相关日志，并更新任务快照。"""

        record = self.append(
            {
                "record_type": "task_event",
                "session_id": session_id,
                "event": deepcopy(event),
            }
        )
        snapshot = {
            "session_id": session_id,
            "last_event": deepcopy(event),
            "updated_at": record["timestamp"],
        }
        self.task_snapshots[session_id] = snapshot
        return record

    def append_device_event(self, device_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
        """追加设备相关日志，并更新设备快照。"""

        record = self.append(
            {
                "record_type": "device_event",
                "device_id": device_id,
                "event": deepcopy(event),
            }
        )
        snapshot = {
            "device_id": device_id,
            "last_event": deepcopy(event),
            "updated_at": record["timestamp"],
        }
        self.device_snapshots[device_id] = snapshot
        return record

    def get_recent_records(self, limit: int = 20) -> List[Dict[str, Any]]:
        """获取最近若干条日志记录。"""

        if limit <= 0:
            return []
        return deepcopy(self.records[-limit:])

    def get_task_snapshot(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取某个任务的最近快照。"""

        snapshot = self.task_snapshots.get(session_id)
        return deepcopy(snapshot) if snapshot else None

    def get_device_snapshot(self, device_id: str) -> Optional[Dict[str, Any]]:
        """获取某个设备的最近快照。"""

        snapshot = self.device_snapshots.get(device_id)
        return deepcopy(snapshot) if snapshot else None
