"""模拟运行时容器启动脚本。"""

import argparse
import time
import threading
from pathlib import Path

from nextgen.integration.container_sim.runtime_ws_server import run_runtime_ws_server
from nextgen.integration.container_sim.services import PeerEndpoints
from nextgen.integration.container_sim.runtime_probe import build_runtime_probe_snapshot, write_runtime_probe_snapshot


def build_argument_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。"""

    parser = argparse.ArgumentParser(description="启动模拟运行时容器。")
    parser.add_argument("--runtime", required=True, help="运行时类型，例如 glass、phone、server。")
    parser.add_argument("--device-id", required=True, help="设备标识。")
    parser.add_argument("--host", default="0.0.0.0", help="WebSocket 服务监听地址。")
    parser.add_argument("--port", type=int, required=True, help="WebSocket 服务监听端口。")
    parser.add_argument("--status-dir", default="/shared/status", help="共享状态目录。")
    parser.add_argument("--server-ws-url", default="ws://server-sim:18083", help="服务器 WebSocket 地址。")
    parser.add_argument("--phone-ws-url", default="ws://phone-sim:18082", help="手机 WebSocket 地址。")
    parser.add_argument("--glass-ws-url", default="ws://glass-sim:18081", help="眼镜 WebSocket 地址。")
    parser.add_argument("--probe-heartbeat-seconds", type=float, default=1.0, help="探针心跳刷新间隔秒数。")
    return parser


def start_probe_heartbeat(runtime: str, device_id: str, status_dir: Path, interval_sec: float) -> threading.Thread:
    """启动探针心跳线程。"""

    def _heartbeat() -> None:
        while True:
            snapshot = build_runtime_probe_snapshot(
                runtime=runtime,
                device_id=device_id,
                metadata={"mode": "container_sim_v5_direct_ws"},
            )
            write_runtime_probe_snapshot(status_dir=status_dir, snapshot=snapshot)
            time.sleep(interval_sec)

    thread = threading.Thread(target=_heartbeat, daemon=True)
    thread.start()
    return thread


def main() -> None:
    """脚本主入口。"""

    parser = build_argument_parser()
    args = parser.parse_args()
    status_dir = Path(args.status_dir)
    snapshot = build_runtime_probe_snapshot(
        runtime=args.runtime,
        device_id=args.device_id,
        metadata={"mode": "container_sim_v5_direct_ws"},
    )
    write_runtime_probe_snapshot(status_dir=status_dir, snapshot=snapshot)
    start_probe_heartbeat(
        runtime=args.runtime,
        device_id=args.device_id,
        status_dir=status_dir,
        interval_sec=args.probe_heartbeat_seconds,
    )
    peers = PeerEndpoints(
        server_ws_url=args.server_ws_url,
        phone_ws_url=args.phone_ws_url,
        glass_ws_url=args.glass_ws_url,
    )
    run_runtime_ws_server(
        runtime=args.runtime,
        device_id=args.device_id,
        host=args.host,
        port=args.port,
        peers=peers,
    )


if __name__ == "__main__":
    main()
