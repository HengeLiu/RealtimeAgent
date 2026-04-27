"""`glass-playback` 命令入口。"""

from __future__ import annotations

import argparse
import json

from openaiglass_glass_playback.config import PlaybackConfig
from openaiglass_glass_playback.glass_device import PlaybackGlassDevice


def run_playback(args: argparse.Namespace) -> int:
    """执行一次设备级 glass 回放。"""

    if not args.config:
        raise ValueError("openaiglass.glass.start --runtime playback 必须提供 --config")
    config = PlaybackConfig.load(args.config, repo_root=args.repo_root)
    device = PlaybackGlassDevice(
        config,
        timeout_seconds=float(args.timeout_seconds),
        max_runtime_seconds=float(args.max_runtime_seconds),
    )
    result = device.run()
    print(
        json.dumps(
            {
                "ok": result.ok,
                "device_id": config.device_id,
                "event_count": result.event_count,
                "actuator_count": result.actuator_count,
            },
            ensure_ascii=False,
        )
    )
    return 0 if result.ok else 1
