#!/usr/bin/env python3
"""手动启动找物体任务的联调脚本。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="手动启动手机端找物体任务")
    parser.add_argument("--host", default="127.0.0.1", help="服务端地址")
    parser.add_argument("--port", type=int, default=8765, help="服务端端口")
    parser.add_argument("--glass-device-id", required=True, help="目标眼镜设备编号")
    parser.add_argument("--target-object", required=True, help="需要寻找的目标物体名称，例如 手机")
    parser.add_argument("--target-ws-uri", default="", help="手机页面显示的 WebSocket 接收地址，留空时自动读取已绑定手机地址")
    parser.add_argument("--frame-interval-ms", type=int, default=500, help="推帧间隔，单位毫秒")
    parser.add_argument("--reason", default="manual_debug", help="启动原因说明")
    parser.add_argument(
        "--reply-output",
        default="runs/debug/find_object_reply.txt",
        help="本地语音回复文本落盘路径；不会发送到眼镜播放",
    )
    parser.add_argument(
        "--task-output",
        default="runs/debug/find_object_task.json",
        help="任务启动响应 JSON 落盘路径",
    )
    return parser.parse_args()


def write_text(path: str, text: str) -> None:
    """把文本写到本地文件。"""

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")


def main() -> int:
    """调用服务端调试接口创建找物体任务。"""

    args = parse_args()
    url = f"http://{args.host}:{args.port}/api/debug/find-object/start"
    payload = {
        "glass_device_id": args.glass_device_id,
        "target_object": args.target_object,
        "frame_interval_ms": args.frame_interval_ms,
        "reason": args.reason,
    }
    if args.target_ws_uri:
        payload["target_ws_uri"] = args.target_ws_uri
    request = Request(
        url=url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP {exc.code}: {error_body}")
        return 1
    except URLError as exc:
        print(f"连接服务端失败: {exc.reason}")
        return 1

    reply_text = str(body.get("reply_text") or "")
    write_text(args.reply_output, reply_text)
    write_text(args.task_output, json.dumps(body, ensure_ascii=False, indent=2))

    print(json.dumps(body, ensure_ascii=False, indent=2))
    print(f"reply_output: {args.reply_output}")
    print(f"task_output: {args.task_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
