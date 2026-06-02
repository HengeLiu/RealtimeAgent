from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


DEFAULT_IOS_PROJECT = "examples/device_app_demo/ios/DeviceDemo.xcodeproj"
DEFAULT_IOS_SCHEME = "DeviceDemo"


def open_ios(argv: list[str] | None = None) -> None:
    """打开 Swift Device SDK Demo 工程。

    主要逻辑：
    1. 解析 SDK Demo iOS 工程路径。
    2. `--print-path` 只输出路径，供无桌面环境和自动验收使用。
    3. 非 `--print-path` 时使用 macOS `open` 打开 Xcode 工程。

    参数：`argv` 为命令行参数。
    返回值：无。
    异常情况：工程不存在、平台不支持或 `open` 命令失败时抛出明确异常。
    """

    parser = argparse.ArgumentParser(prog="realtime-agent.ios.open", description="打开 Swift Device SDK Demo 工程")
    parser.add_argument("--project", default=DEFAULT_IOS_PROJECT, help="Xcode 工程路径")
    parser.add_argument("--print-path", action="store_true", help="只打印工程路径，不打开 Xcode")
    args = parser.parse_args(argv)

    project = _resolve_audio_root_path(args.project)
    if not project.exists():
        raise FileNotFoundError(f"iOS project not found: {project}")
    if args.print_path:
        print(project)
        return
    if sys.platform != "darwin":
        raise RuntimeError("realtime-agent.ios.open 需要 macOS；无桌面环境请使用 --print-path 查看工程路径")
    subprocess.run(["open", str(project)], check=True)
    print(project)


def build_sim(argv: list[str] | None = None) -> None:
    """构建 Swift Device SDK Demo。

    主要逻辑：
    1. 检查 Xcode 工程与 `xcodebuild` 是否存在。
    2. 支持 `--dry-run` 输出将执行的命令，避免自动验收依赖 Xcode。
    3. 真正构建时转交给 `xcodebuild`。

    参数：`argv` 为命令行参数。
    返回值：无。
    异常情况：缺少 Xcode 或构建失败时抛出明确异常。
    """

    parser = argparse.ArgumentParser(prog="realtime-agent.ios.build-sim", description="构建 Swift Device SDK Demo")
    parser.add_argument("--project", default=DEFAULT_IOS_PROJECT, help="Xcode 工程路径")
    parser.add_argument("--scheme", default=DEFAULT_IOS_SCHEME)
    parser.add_argument("--destination", default="generic/platform=iOS Simulator")
    parser.add_argument("--dry-run", action="store_true", help="只输出 xcodebuild 命令，不执行构建")
    args = parser.parse_args(argv)

    project = _resolve_audio_root_path(args.project)
    if not project.exists():
        raise FileNotFoundError(f"iOS project not found: {project}")
    command = ["xcodebuild", "-project", str(project), "-scheme", args.scheme, "-destination", args.destination, "build"]
    if args.dry_run:
        print(" ".join(command))
        return
    if shutil.which("xcodebuild") is None:
        raise RuntimeError("xcodebuild not found；请安装 Xcode，或使用 --dry-run 检查命令")
    subprocess.run(command, check=True)


def _resolve_audio_root_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    if path.exists():
        return path.resolve()
    audio_root = Path(__file__).resolve().parents[3]
    return (audio_root / path).resolve()
