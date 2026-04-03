"""容器运行时探针支持。"""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Optional


@dataclass
class RuntimeProbeSnapshot:
    """容器运行时探针快照。

    主要功能：
    - 描述一个模拟运行时容器的基础就绪状态
    - 为容器级模拟提供最小状态同步手段

    主要属性：
    - runtime：运行时类型
    - device_id：设备标识
    - status：当前状态
    - timestamp：最近更新时间
    """

    runtime: str
    device_id: str
    status: str
    timestamp: str
    metadata: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        """将探针快照转换为字典。"""

        return {
            "runtime": self.runtime,
            "device_id": self.device_id,
            "status": self.status,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


def build_runtime_probe_snapshot(
    runtime: str,
    device_id: str,
    status: str = "ready",
    metadata: Optional[Dict[str, str]] = None,
) -> RuntimeProbeSnapshot:
    """构造容器运行时探针快照。"""

    return RuntimeProbeSnapshot(
        runtime=runtime,
        device_id=device_id,
        status=status,
        timestamp=datetime.now().astimezone().isoformat(),
        metadata=metadata or {},
    )


def write_runtime_probe_snapshot(status_dir: Path, snapshot: RuntimeProbeSnapshot) -> Path:
    """写出探针快照文件。

    参数：
    - status_dir：共享状态目录
    - snapshot：待写出的探针快照

    返回值：
    - 写出的文件路径
    """

    status_dir.mkdir(parents=True, exist_ok=True)
    path = status_dir / f"{snapshot.runtime}.json"
    path.write_text(json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_runtime_probe_snapshot(status_dir: Path, runtime: str) -> RuntimeProbeSnapshot:
    """读取指定运行时的探针快照。"""

    path = status_dir / f"{runtime}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return RuntimeProbeSnapshot(
        runtime=payload["runtime"],
        device_id=payload["device_id"],
        status=payload["status"],
        timestamp=payload["timestamp"],
        metadata=payload.get("metadata", {}),
    )


def wait_for_runtime_probe_snapshots(
    status_dir: Path,
    runtimes: Iterable[str],
    timeout_sec: float = 10.0,
    poll_interval_sec: float = 0.1,
) -> Dict[str, RuntimeProbeSnapshot]:
    """等待一组运行时探针文件准备完成。

    参数：
    - status_dir：共享状态目录
    - runtimes：需要等待的运行时名称集合
    - timeout_sec：最长等待秒数
    - poll_interval_sec：轮询间隔秒数

    返回值：
    - 运行时名称到探针快照的映射

    异常情况：
    - 超时仍未等到全部探针文件时抛出 `TimeoutError`
    """

    expected = list(runtimes)
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        found: Dict[str, RuntimeProbeSnapshot] = {}
        for runtime in expected:
            path = status_dir / f"{runtime}.json"
            if path.exists():
                found[runtime] = load_runtime_probe_snapshot(status_dir, runtime)
        if len(found) == len(expected):
            return found
        time.sleep(poll_interval_sec)
    raise TimeoutError(f"未在 {timeout_sec} 秒内等到全部运行时探针文件。")
