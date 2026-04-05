"""启动眼镜控制面参考实现。"""

import argparse
import json
import socket
import sys
import threading
import time
from ipaddress import ip_address
from pathlib import Path

import uvicorn


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nextgen.apps.glass.runtime.app import GlassRuntimeApp
from nextgen.apps.glass.runtime.http_control_app import build_glass_control_app
from nextgen.shared.utils.http import post_json
from nextgen.shared.utils.logging_utils import setup_file_logger


def detect_preferred_ipv4() -> str:
    """探测当前机器优先使用的局域网 IPv4 地址。"""

    def _is_preferred(address: str) -> bool:
        try:
            parsed = ip_address(address)
        except ValueError:
            return False
        if parsed.version != 4:
            return False
        if parsed.is_loopback or parsed.is_link_local:
            return False
        if address.startswith("198.18.") or address.startswith("198.19."):
            return False
        return parsed.is_private

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.connect(("8.8.8.8", 80))
            address = client.getsockname()[0]
            if _is_preferred(address):
                return address
    except OSError:
        pass

    try:
        hostname = socket.gethostname()
        for _, _, _, _, sockaddr in socket.getaddrinfo(hostname, None, socket.AF_INET):
            address = sockaddr[0]
            if _is_preferred(address):
                return address
    except OSError:
        pass

    return "127.0.0.1"


def build_argument_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。"""

    parser = argparse.ArgumentParser(description="启动 nextgen 眼镜控制面。")
    parser.add_argument("--device-id", default="glass-001")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=18491)
    parser.add_argument("--advertise-host", default="")
    parser.add_argument("--server-base-url", default="")
    parser.add_argument("--server-port", type=int, default=18490)
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
                    runtime.mark_registration_success("register")
                    runtime.logger.info(
                        "注册眼镜设备(register_device) %s",
                        json.dumps({"device_id": runtime.device_id, "server_base_url": server_base_url}, ensure_ascii=False),
                    )
                    registered = True
                else:
                    post_json(f"{server_base_url}/devices/heartbeat", runtime.build_heartbeat_payload())
                    runtime.mark_registration_success("heartbeat")
                    runtime.logger.debug(
                        "发送眼镜心跳(device_heartbeat) %s",
                        json.dumps({"device_id": runtime.device_id, "server_base_url": server_base_url}, ensure_ascii=False),
                    )
            except Exception as exc:
                runtime.mark_registration_failure("register" if not registered else "heartbeat", str(exc))
                runtime.logger.error(
                    "眼镜注册或心跳失败(register_or_heartbeat_failed) %s",
                    json.dumps(
                        {"device_id": runtime.device_id, "server_base_url": server_base_url, "reason": str(exc)},
                        ensure_ascii=False,
                    ),
                )
                registered = False
            time.sleep(interval_sec)

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    return thread


def main() -> None:
    """脚本主入口。"""

    args = build_argument_parser().parse_args()
    setup_file_logger("nextgen.glass.runtime", args.log_file)
    advertise_host = args.advertise_host.strip() or detect_preferred_ipv4()
    server_base_url = args.server_base_url.strip() or f"http://127.0.0.1:{args.server_port}"
    bootstrap_logger = setup_file_logger("nextgen.glass.runtime.bootstrap", args.log_file)
    bootstrap_logger.info(
        "启动眼镜控制面(glass_bootstrap) %s",
        {
            "device_id": args.device_id,
            "host": args.host,
            "port": args.port,
            "advertise_host": advertise_host,
            "server_base_url": server_base_url,
            "same_machine_server": True,
            "ui_url": f"http://127.0.0.1:{args.port}/",
        },
    )
    runtime = GlassRuntimeApp(device_id=args.device_id)
    runtime.start()
    runtime.enable_local_microphone()
    runtime.enable_local_speaker()
    runtime.configure_control_endpoint(host=advertise_host, port=args.port)
    runtime.configure_server_base_url(server_base_url)
    start_registration_loop(runtime, server_base_url, args.heartbeat_seconds)
    uvicorn.run(build_glass_control_app(runtime), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
