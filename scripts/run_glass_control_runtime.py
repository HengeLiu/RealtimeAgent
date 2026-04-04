"""启动眼镜控制面参考实现。"""

import argparse
import sys
import threading
import time
from pathlib import Path

import uvicorn


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nextgen.apps.glass.runtime.app import GlassRuntimeApp
from nextgen.apps.glass.runtime.http_control_app import build_glass_control_app
from nextgen.shared.utils.http import post_json
from nextgen.shared.utils.logging_utils import setup_file_logger


def build_argument_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。"""

    parser = argparse.ArgumentParser(description="启动 nextgen 眼镜控制面。")
    parser.add_argument("--device-id", default="glass-001")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18091)
    parser.add_argument("--advertise-host", default="127.0.0.1")
    parser.add_argument("--server-base-url", default="http://127.0.0.1:18090")
    parser.add_argument("--heartbeat-seconds", type=float, default=2.0)
    parser.add_argument("--log-file", default="nextgen/apps/glass/logs/glass-runtime.log")
    return parser


def start_registration_loop(runtime: GlassRuntimeApp, server_base_url: str, interval_sec: float) -> threading.Thread:
    """启动注册与心跳线程。"""

    def _loop() -> None:
        registered = False
        while True:
            try:
                if not registered:
                    post_json(f"{server_base_url}/devices/register", runtime.build_registration_payload())
                    registered = True
                else:
                    post_json(f"{server_base_url}/devices/heartbeat", runtime.build_heartbeat_payload())
            except Exception:
                registered = False
            time.sleep(interval_sec)

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    return thread


def main() -> None:
    """脚本主入口。"""

    args = build_argument_parser().parse_args()
    setup_file_logger("nextgen.glass.runtime", args.log_file)
    runtime = GlassRuntimeApp(device_id=args.device_id)
    runtime.start()
    runtime.enable_local_microphone()
    runtime.enable_local_speaker()
    runtime.configure_control_endpoint(host=args.advertise_host, port=args.port)
    runtime.configure_server_base_url(args.server_base_url)
    start_registration_loop(runtime, args.server_base_url, args.heartbeat_seconds)
    uvicorn.run(build_glass_control_app(runtime), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
