"""容器级找物场景运行支持。"""

import json
from pathlib import Path
from typing import Any, Dict, Iterable

from nextgen.integration.container_sim.runtime_probe import wait_for_runtime_probe_snapshots
from nextgen.integration.smoke.process_simulator import ThreeProcessFindObjectSimulator


def run_containerized_find_object_case(
    case_id: str,
    status_dir: Path,
    required_runtimes: Iterable[str] = ("glass", "phone", "server"),
) -> Dict[str, Any]:
    """运行容器级找物标准场景。

    主要逻辑：
    - 等待三端容器探针文件就绪
    - 调用三进程模拟器执行标准 case
    - 将探针快照与运行结果合并返回
    """

    probes = wait_for_runtime_probe_snapshots(
        status_dir=status_dir,
        runtimes=required_runtimes,
    )
    simulator = ThreeProcessFindObjectSimulator()
    report = simulator.run_case(case_id)
    report["container_probes"] = {runtime: snapshot.to_dict() for runtime, snapshot in probes.items()}
    return report


def write_containerized_find_object_report(output_path: Path, report: Dict[str, Any]) -> None:
    """将容器级找物运行结果写入文件。"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
