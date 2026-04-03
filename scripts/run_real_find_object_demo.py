"""真实场景找物通讯三进程 demo。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nextgen.shared.utils.http import get_json, post_json, wait_for_http_ready


def build_argument_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。"""

    parser = argparse.ArgumentParser(description="启动真实场景找物通讯三进程 demo。")
    parser.add_argument("--server-port", type=int, default=18090)
    parser.add_argument("--glass-port", type=int, default=18091)
    parser.add_argument("--phone-port", type=int, default=18092)
    parser.add_argument("--startup-timeout", type=float, default=12.0)
    return parser


def start_process(cmd: List[str]) -> subprocess.Popen:
    """启动子进程。"""

    return subprocess.Popen(cmd, cwd=PROJECT_ROOT)


def build_demo_analysis() -> Dict[str, Any]:
    """构造默认的找物单帧分析输入。"""

    return {
        "frame_width": 320,
        "frame_height": 240,
        "found": True,
        "candidate_count": 1,
        "source": "real_control_plane_demo",
        "object_observation": {
            "center_x": 250.0,
            "center_y": 120.0,
            "area": 3200.0,
            "polygon": [[220.0, 90.0], [280.0, 90.0], [280.0, 150.0], [220.0, 150.0]],
            "score": 0.96,
            "position": "right",
        },
        "hand_observation": None,
    }


def main() -> None:
    """脚本主入口。"""

    args = build_argument_parser().parse_args()
    server_base_url = f"http://127.0.0.1:{args.server_port}"

    processes = [
        start_process([sys.executable, "scripts/run_server_control_runtime.py", "--host", "127.0.0.1", "--port", str(args.server_port)]),
        start_process(
            [
                sys.executable,
                "scripts/run_glass_control_runtime.py",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.glass_port),
                "--advertise-host",
                "127.0.0.1",
                "--server-base-url",
                server_base_url,
            ]
        ),
        start_process(
            [
                sys.executable,
                "scripts/run_phone_control_runtime.py",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.phone_port),
                "--advertise-host",
                "127.0.0.1",
                "--server-base-url",
                server_base_url,
            ]
        ),
    ]

    try:
        wait_for_http_ready(f"{server_base_url}/health", timeout_sec=args.startup_timeout)
        wait_for_http_ready(f"http://127.0.0.1:{args.glass_port}/health", timeout_sec=args.startup_timeout)
        wait_for_http_ready(f"http://127.0.0.1:{args.phone_port}/health", timeout_sec=args.startup_timeout)

        deadline = time.time() + args.startup_timeout
        devices: List[Dict[str, Any]] = []
        while time.time() < deadline:
            snapshot = get_json(f"{server_base_url}/snapshot")
            devices = snapshot.get("devices", [])
            if len(devices) >= 2:
                break
            time.sleep(0.3)
        if len(devices) < 2:
            raise RuntimeError("未在预期时间内完成设备注册。")

        created = post_json(
            f"{server_base_url}/tasks/create-session",
            {
                "task_name": "find_object",
                "glass_device_id": "glass-001",
                "phone_device_id": "phone-001",
                "input": {"target_name": "手机"},
            },
        )
        session_id = created["session"]["session_id"]
        orchestrated = post_json(
            f"{server_base_url}/tasks/{session_id}/peer-link/orchestrate",
            {"stream_type": "image_stream"},
        )
        frame_result = post_json(
            f"http://127.0.0.1:{args.glass_port}/device-api/task/send-frame-analysis",
            {
                "task_session_id": session_id,
                "target_name": "手机",
                "analysis": build_demo_analysis(),
                "mark_completed": True,
            },
        )
        stopped = post_json(f"{server_base_url}/tasks/{session_id}/peer-link/stop-and-notify", {})
        snapshot = get_json(f"{server_base_url}/snapshot")
        report = {
            "server_base_url": server_base_url,
            "created": created,
            "orchestrated": orchestrated,
            "frame_result": frame_result,
            "stopped": stopped,
            "health_snapshot": snapshot,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        for process in reversed(processes):
            process.terminate()
        for process in reversed(processes):
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    main()
