#!/usr/bin/env python3
"""手动启动眼镜到手机视频直连任务的联调脚本。"""
# 示例：
# python scripts/start_phone_video_link.py --host 127.0.0.1 --port 8765 --glass-device-id glass-001 --frame-interval-ms 100

from __future__ import annotations

import argparse
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def parse_args() -> argparse.Namespace:
    """解析命令行参数。

    返回值：
    1. 当前脚本所需的联调参数对象。
    """

    parser = argparse.ArgumentParser(description="手动启动眼镜到手机的视频直连任务")
    parser.add_argument("--host", default="127.0.0.1", help="服务端地址")
    parser.add_argument("--port", type=int, default=8765, help="服务端端口")
    parser.add_argument("--glass-device-id", required=True, help="目标眼镜设备编号")
    parser.add_argument("--target-ws-uri", default="", help="手机页面显示的 WebSocket 接收地址，留空时自动读取已绑定手机地址")
    parser.add_argument("--frame-interval-ms", type=int, default=500, help="推帧间隔，单位毫秒")
    parser.add_argument("--reason", default="manual_debug", help="启动原因说明")
    return parser.parse_args()


def main() -> int:
    """调用服务端调试接口创建视频直连任务。

    主要逻辑：
    1. 组装 JSON 请求体。
    2. 调用服务端 `/api/debug/phone-video-link/start`。
    3. 若未显式传入 `target_ws_uri`，则由服务端根据已绑定手机自动解析地址。
    4. 打印任务编号与目标地址，方便继续观察眼镜和手机日志。

    返回值：
    1. 成功时返回 0。
    2. 服务端返回错误时抛出异常并由解释器输出。
    """

    args = parse_args()
    url = f"http://{args.host}:{args.port}/api/debug/phone-video-link/start"
    payload = {
        "glass_device_id": args.glass_device_id,
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
    print(json.dumps(body, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
