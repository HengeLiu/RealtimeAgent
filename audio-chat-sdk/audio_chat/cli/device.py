from __future__ import annotations

import argparse
import json
from pathlib import Path

from audio_chat.device_capabilities import compile_device_capabilities_file


def validate(argv: list[str] | None = None) -> None:
    """校验设备能力文件并输出编译结果。

    主要逻辑：
    1. 读取端侧 YAML/JSON 设备能力文件。
    2. 校验标准语义 ID 和参数类型。
    3. 输出 server 注册时使用的结构化 supports。

    参数：`argv` 为命令行参数。
    返回值：无。
    异常情况：文件不存在、语义 ID 错误或参数非法时抛出异常并让命令失败。
    """

    parser = argparse.ArgumentParser(prog="audio-chat.device.validate", description="校验 audio-chat 设备能力文件")
    parser.add_argument("path", help="设备能力 YAML/JSON 文件路径")
    parser.add_argument("--json", action="store_true", help="只输出 JSON 结果")
    args = parser.parse_args(argv)

    result = compile_device_capabilities_file(Path(args.path))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f"device capability file ok: {args.path}")
    print("compiled registration payload:")
    print(json.dumps(result["payload"], ensure_ascii=False, indent=2))
