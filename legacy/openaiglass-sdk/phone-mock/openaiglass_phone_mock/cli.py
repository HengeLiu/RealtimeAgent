"""`phone-mock` 命令入口。"""

from __future__ import annotations

import argparse
import json

from openaiglass_phone_mock.config import PhoneMockConfig
from openaiglass_phone_mock.phone_device import PhoneMockDevice


def run_phone_mock(args: argparse.Namespace) -> int:
    """启动 `phone-mock` 虚拟手机设备。"""

    if not args.config:
        raise ValueError("openaiglass.phone.mock 必须提供 --config")
    config = PhoneMockConfig.load(args.config, repo_root=args.repo_root)
    device = PhoneMockDevice(
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
                "received_task_count": result.received_task_count,
                "reported_event_count": result.reported_event_count,
            },
            ensure_ascii=False,
        )
    )
    return 0 if result.ok else 1
