"""容器级模拟支持测试。"""

import tempfile
import threading
import time
import socket
from pathlib import Path

from nextgen.integration.container_sim.case_runner import run_containerized_find_object_case
from nextgen.integration.container_sim.runtime_probe import (
    build_runtime_probe_snapshot,
    load_runtime_probe_snapshot,
    wait_for_runtime_probe_snapshots,
    write_runtime_probe_snapshot,
)
from nextgen.integration.container_sim.runtime_ws_server import run_runtime_ws_server
from nextgen.integration.container_sim.services import PeerEndpoints
from nextgen.integration.container_sim.ws_client import WebSocketRpcClient, wait_for_ws_ready


def _find_free_port() -> int:
    """查找一个当前可用的本地端口。"""

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_runtime_server_thread(runtime: str, device_id: str, host: str, port: int, peers: PeerEndpoints) -> threading.Thread:
    """启动某个运行时的直连 WebSocket 服务线程。"""

    thread = threading.Thread(
        target=run_runtime_ws_server,
        kwargs={
            "runtime": runtime,
            "device_id": device_id,
            "host": host,
            "port": port,
            "peers": peers,
        },
        daemon=True,
    )
    thread.start()
    time.sleep(0.2)
    return thread


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
    """验证容器级场景运行结果会包含探针和三端直连 WebSocket 结果。"""

    with tempfile.TemporaryDirectory() as temp_dir:
        root_dir = Path(temp_dir)
        status_dir = root_dir / "status"
        glass_port = _find_free_port()
        phone_port = _find_free_port()
        server_port = _find_free_port()
        peers = PeerEndpoints(
            server_ws_url=f"ws://127.0.0.1:{server_port}",
            phone_ws_url=f"ws://127.0.0.1:{phone_port}",
            glass_ws_url=f"ws://127.0.0.1:{glass_port}",
        )
        for runtime, device_id in (
            ("glass", "glass-001"),
            ("phone", "phone-001"),
            ("server", "server-main"),
        ):
            snapshot = build_runtime_probe_snapshot(runtime=runtime, device_id=device_id)
            write_runtime_probe_snapshot(status_dir=status_dir, snapshot=snapshot)
        threads = [
            _start_runtime_server_thread("glass", "glass-001", "127.0.0.1", glass_port, peers),
            _start_runtime_server_thread("phone", "phone-001", "127.0.0.1", phone_port, peers),
            _start_runtime_server_thread("server", "server-main", "127.0.0.1", server_port, peers),
        ]
        try:
            report = run_containerized_find_object_case(
                case_id="find_object_phone_center_001",
                status_dir=status_dir,
                glass_ws_url=peers.glass_ws_url,
                server_ws_url=peers.server_ws_url,
                require_fresh_probes=False,
            )
        finally:
            for thread in threads:
                thread.join(timeout=1.0)

    assert report["session_id"].startswith("tasksess_")
    assert set(report["container_probes"].keys()) == {"glass", "phone", "server"}
    assert report["server_report"]["session"]["status"] == "completed"
    assert "已发现手机" in report["frame_response"]["phone_response"]["hint"]["text"]


def test_runtime_ws_server_health_endpoint_returns_ok() -> None:
    """验证运行时直连 WebSocket 服务可正常响应健康检查。"""

    glass_port = _find_free_port()
    peers = PeerEndpoints(
        server_ws_url="ws://127.0.0.1:19999",
        phone_ws_url="ws://127.0.0.1:19998",
        glass_ws_url=f"ws://127.0.0.1:{glass_port}",
    )
    _start_runtime_server_thread("glass", "glass-001", "127.0.0.1", glass_port, peers)
    wait_for_ws_ready(peers.glass_ws_url)
    client = WebSocketRpcClient(peers.glass_ws_url)
    try:
        payload = client.request("/health", {})
    finally:
        client.close()

    assert payload["status"] == "ok"
    assert payload["runtime"] == "glass"
