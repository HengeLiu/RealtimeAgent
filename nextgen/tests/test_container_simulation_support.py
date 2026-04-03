"""容器级模拟支持测试。"""

import tempfile
from pathlib import Path

from nextgen.integration.container_sim.case_runner import run_containerized_find_object_case
from nextgen.integration.container_sim.runtime_probe import (
    build_runtime_probe_snapshot,
    load_runtime_probe_snapshot,
    wait_for_runtime_probe_snapshots,
    write_runtime_probe_snapshot,
)


def test_runtime_probe_snapshot_can_be_written_and_loaded() -> None:
    """验证探针快照可以正确写入和读回。"""

    with tempfile.TemporaryDirectory() as temp_dir:
        status_dir = Path(temp_dir)
        snapshot = build_runtime_probe_snapshot(runtime="glass", device_id="glass-001")
        write_runtime_probe_snapshot(status_dir=status_dir, snapshot=snapshot)
        loaded = load_runtime_probe_snapshot(status_dir=status_dir, runtime="glass")

    assert loaded.runtime == "glass"
    assert loaded.device_id == "glass-001"
    assert loaded.status == "ready"


def test_wait_for_runtime_probe_snapshots_returns_all_runtimes() -> None:
    """验证等待探针方法可以返回全部运行时。"""

    with tempfile.TemporaryDirectory() as temp_dir:
        status_dir = Path(temp_dir)
        for runtime, device_id in (
            ("glass", "glass-001"),
            ("phone", "phone-001"),
            ("server", "server-main"),
        ):
            snapshot = build_runtime_probe_snapshot(runtime=runtime, device_id=device_id)
            write_runtime_probe_snapshot(status_dir=status_dir, snapshot=snapshot)
        found = wait_for_runtime_probe_snapshots(status_dir=status_dir, runtimes=("glass", "phone", "server"))

    assert set(found.keys()) == {"glass", "phone", "server"}


def test_run_containerized_find_object_case_merges_probe_and_result() -> None:
    """验证容器级场景运行结果会包含探针和场景结果。"""

    with tempfile.TemporaryDirectory() as temp_dir:
        status_dir = Path(temp_dir)
        for runtime, device_id in (
            ("glass", "glass-001"),
            ("phone", "phone-001"),
            ("server", "server-main"),
        ):
            snapshot = build_runtime_probe_snapshot(runtime=runtime, device_id=device_id)
            write_runtime_probe_snapshot(status_dir=status_dir, snapshot=snapshot)
        report = run_containerized_find_object_case(
            case_id="find_object_phone_center_001",
            status_dir=status_dir,
        )

    assert report["result"]["final_status"] == "completed"
    assert set(report["container_probes"].keys()) == {"glass", "phone", "server"}
