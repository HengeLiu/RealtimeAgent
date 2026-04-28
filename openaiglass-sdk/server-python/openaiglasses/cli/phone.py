"""手机端通用启动命令。"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    """构建手机端命令参数解析器。"""

    parser = argparse.ArgumentParser(prog="openaiglass.phone", description="打开、构建或启动 OpenAI Glasses 手机端设备")
    parser.add_argument("action", nargs="?", default="open", choices=["config", "open", "build-sim", "build-device", "mock"])
    parser.add_argument("--repo-root", default="", help="仓库根目录；仅作为本仓库开发时的默认路径锚点")
    parser.add_argument("--sdk-root", default="", help="SDK 源码根目录，用于查找 phone-mock 组件")
    parser.add_argument("--app-root", default="openaiglass-for-blind", help="业务工程根目录")
    parser.add_argument("--phone-project", default="", help="iOS xcodeproj 路径")
    parser.add_argument("--phone-scheme", default="GlassesVideoReceiver", help="iOS scheme")
    parser.add_argument("--configuration", default="Debug", help="Xcode 构建配置")
    parser.add_argument("--destination", default="", help="xcodebuild destination")
    parser.add_argument("--sync-script", default="", help="业务配置同步脚本")
    parser.add_argument("--server-config", default="", help="服务端 env 配置文件")
    parser.add_argument("--public-host", default="", help="手动指定局域网服务端地址")
    parser.add_argument("--config", default="", help="phone-mock JSON 配置文件")
    parser.add_argument("--timeout-seconds", type=float, default=30.0, help="phone-mock 网络超时时间")
    parser.add_argument("--max-runtime-seconds", type=float, default=0.0, help="phone-mock 最大运行时长，0 表示持续运行")
    return parser


def main(argv: list[str] | None = None) -> int:
    """手机端命令主入口。"""

    args = build_parser().parse_args(argv)
    if args.action == "mock":
        return run_phone_mock(args)
    app_root = Path(args.app_root).resolve()
    ensure_local_configs(app_root)
    if args.action == "config":
        print_config_instructions(app_root)
        return sync_config(args, app_root)
    code = sync_config(args, app_root)
    if code != 0:
        return code
    if args.action == "open":
        return open_project(resolve_phone_project(args, app_root))
    if args.action == "build-sim":
        return build_sim(args, resolve_phone_project(args, app_root))
    if args.action == "build-device":
        return build_device(args, resolve_phone_project(args, app_root))
    return 2


def run_phone_mock(args: argparse.Namespace) -> int:
    """启动 `phone-mock` 虚拟手机设备。"""

    repo_root = resolve_repo_root(args)
    sdk_root = Path(args.sdk_root).resolve() if args.sdk_root else repo_root / "openaiglass-sdk"
    phone_mock_root = sdk_root / "phone-mock"
    app_root = Path(args.app_root).resolve() if args.app_root else repo_root / "openaiglass-for-blind"
    if str(phone_mock_root) not in sys.path:
        sys.path.insert(0, str(phone_mock_root))
    if str(app_root) not in sys.path:
        sys.path.insert(0, str(app_root))
    if not args.config:
        args.config = str(app_root / "host/phone-mock/config/phone.mock.json")
    args.repo_root = str(repo_root)
    from openaiglass_phone_mock.cli import run_phone_mock as run_device

    return run_device(args)


def resolve_repo_root(args: argparse.Namespace) -> Path:
    """解析仓库根目录。"""

    if args.repo_root:
        return Path(args.repo_root).resolve()
    current = Path.cwd().resolve()
    if (current / "openaiglass-sdk").exists():
        return current
    return current


def resolve_phone_project(args: argparse.Namespace, app_root: Path) -> Path:
    """解析 iOS 工程路径。"""

    if args.phone_project:
        return Path(args.phone_project).resolve()
    return app_root / "host/phone/ios/GlassesVideoReceiver.xcodeproj"


def ensure_file_from_template(target: Path, template: Path, label: str) -> bool:
    """确保本地配置文件存在，不存在时从模板复制。"""

    if target.exists():
        return False
    if not template.exists():
        raise RuntimeError(f"{label}模板不存在: {template}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(template, target)
    print(f"[config] 已创建{label}: {target}")
    return True


def ensure_local_configs(app_root: Path) -> None:
    """确保业务侧三端本地配置文件存在。"""

    initialized = False
    initialized |= ensure_file_from_template(
        app_root / "config/local_server.env",
        app_root / "config/local_server.env.example",
        "服务端本地配置",
    )
    initialized |= ensure_file_from_template(
        app_root / "host/phone/config/AppConfig.plist",
        app_root / "host/phone/config/AppConfig.plist.example",
        "手机本地配置",
    )
    initialized |= ensure_file_from_template(
        app_root / "host/glass/config/local_build.env",
        app_root / "host/glass/config/local_build.env.example",
        "眼镜本地配置",
    )
    if initialized:
        print("[config] 首次运行已从模板初始化配置。")
        print_config_instructions(app_root)
        raise SystemExit(2)


def print_config_instructions(app_root: Path) -> None:
    """打印三端联调配置说明。"""

    print(
        f"""[config] 请在配置文件中修改真机联调参数，不要通过环境变量临时覆盖：

  文件 1: {app_root / "config/local_server.env"}
    SERVER_PUBLIC_HOST 无需手动修改，命令会自动探测并回写 Mac 当前局域网 IPv4
    PORT="8765"
    DEVICE_TOKEN_MAP="glass-001=pair-demo-token,phone-001=pair-phone-token"

  文件 2: {app_root / "host/phone/config/AppConfig.plist"}
    命令会根据服务端配置自动写入 serverBaseURLString、phoneDeviceID、pairToken、desiredGlassDeviceID

  文件 3: {app_root / "host/glass/config/local_build.env"}
    GLASS_WIFI_PRIMARY_SSID="你的 Wi-Fi 名称"
    GLASS_WIFI_PRIMARY_PASSWORD="你的 Wi-Fi 密码"
    命令会根据服务端配置自动写入 GLASS_SERVER_WS_URI、GLASS_DEVICE_ID、GLASS_PAIR_TOKEN
"""
    )


def sync_config(args: argparse.Namespace, app_root: Path) -> int:
    """同步三端联调配置。"""

    if args.sync_script:
        sync_script = Path(args.sync_script).resolve()
        if not sync_script.exists():
            raise RuntimeError(f"配置同步脚本不存在: {sync_script}")
        command = [sys.executable, str(sync_script)]
        server_config = args.server_config or str(app_root / "config/local_server.env")
        command.extend(["--server-config", server_config])
        if args.public_host:
            command.extend(["--public-host", args.public_host])
        return subprocess.run(command, check=False).returncode

    from openaiglasses.cli.config import main as config_main

    command = ["sync", "--app-root", str(app_root)]
    server_config = args.server_config or str(app_root / "config/local_server.env")
    command.extend(["--server-config", server_config])
    if args.public_host:
        command.extend(["--public-host", args.public_host])
    return config_main(command)


def open_project(project: Path) -> int:
    """打开 iOS 工程。"""

    if not project.exists():
        raise RuntimeError(f"iOS 工程不存在: {project}")
    print(f"[open] 打开 iOS 工程: {project}")
    subprocess.Popen(
        ["open", "-a", "Xcode", str(project)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return 0


def build_sim(args: argparse.Namespace, project: Path) -> int:
    """构建 iOS 模拟器目标。"""

    command = [
        "xcodebuild",
        "-project",
        str(project),
        "-scheme",
        args.phone_scheme,
        "-sdk",
        "iphonesimulator",
        "-configuration",
        args.configuration,
        "build",
        "CODE_SIGNING_ALLOWED=NO",
    ]
    return subprocess.run(command, check=False).returncode


def build_device(args: argparse.Namespace, project: Path) -> int:
    """构建 iOS 真机目标。"""

    destination = args.destination or "generic/platform=iOS"
    command = [
        "xcodebuild",
        "-project",
        str(project),
        "-scheme",
        args.phone_scheme,
        "-configuration",
        args.configuration,
        "-destination",
        destination,
        "build",
    ]
    return subprocess.run(command, check=False).returncode
