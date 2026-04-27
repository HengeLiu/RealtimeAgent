"""眼镜端通用启动命令。"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

from openaiglasses.cli.common import read_env_file


def build_parser() -> argparse.ArgumentParser:
    """构建眼镜端命令参数解析器。"""

    parser = argparse.ArgumentParser(prog="openaiglass.glass.start", description="启动 OpenAI Glasses 眼镜端运行时")
    parser.add_argument("runtime_arg", nargs="?", choices=["firmware", "playback"], help="兼容旧形式的位置运行时类型")
    parser.add_argument("--runtime", choices=["firmware", "playback"], default="", help="眼镜运行时类型")
    parser.add_argument("--repo-root", default=".", help="项目根目录")
    parser.add_argument("--project-dir", default="", help="ESP-IDF 工程目录")
    parser.add_argument("--idf-root", default="", help="ESP-IDF 安装目录")
    parser.add_argument("--target", default="esp32s3", help="ESP-IDF target")
    parser.add_argument("-p", "--port", default="", help="串口端口")
    parser.add_argument("-b", "--baud", default="115200", help="串口波特率")
    parser.add_argument("--build-dir", default="", help="构建目录")
    parser.add_argument("--config", default="", help="眼镜本地 env 配置文件")
    parser.add_argument("--sdkconfig", default="", help="生成的 sdkconfig 路径")
    parser.add_argument("--sdkconfig-defaults", default="", help="sdkconfig defaults 路径")
    parser.add_argument("--esp-python", default="", help="ESP-IDF Python 解释器")
    parser.add_argument("-t", "--set-target", action="store_true", help="强制执行 idf.py set-target")
    parser.add_argument("-c", "--clean", action="store_true", help="执行 idf.py fullclean")
    parser.add_argument("-e", "--erase-flash", action="store_true", help="烧录前擦除 flash")
    parser.add_argument("--build-only", action="store_true", help="仅编译")
    parser.add_argument("--flash-only", action="store_true", help="仅烧录")
    parser.add_argument("--monitor-only", action="store_true", help="仅监看串口")
    parser.add_argument("--timeout-seconds", type=float, default=30.0, help="playback 网络超时时间")
    parser.add_argument("--max-runtime-seconds", type=float, default=30.0, help="playback 触发音频发送后继续等待控制消息的时间")
    return parser


def main(argv: list[str] | None = None) -> int:
    """眼镜端命令主入口。"""

    args = build_parser().parse_args(argv)
    args.runtime = args.runtime or args.runtime_arg or "firmware"
    if args.runtime == "playback":
        from openaiglasses.playback.cli import run_playback

        return run_playback(args)
    return run_firmware(args)


def run_firmware(args: argparse.Namespace) -> int:
    """执行 ESP-IDF 固件构建、烧录和监看。"""

    repo_root = Path(args.repo_root).resolve()
    project_dir = Path(args.project_dir).resolve() if args.project_dir else repo_root / "openaiglass-sdk/glass-esp32"
    idf_root = Path(args.idf_root).resolve() if args.idf_root else repo_root / ".cache/esp-idf-v5.3.2"
    build_dir = Path(args.build_dir).resolve() if args.build_dir else project_dir / "build"
    config_file = Path(args.config).resolve() if args.config else repo_root / "openaiglass-for-blind/host/glass/config/local_build.env"
    sdkconfig_file = Path(args.sdkconfig).resolve() if args.sdkconfig else project_dir / "sdkconfig.local"
    sdkconfig_defaults = (
        Path(args.sdkconfig_defaults).resolve() if args.sdkconfig_defaults else project_dir / "sdkconfig.defaults"
    )
    if not idf_root.exists():
        raise RuntimeError(f"ESP-IDF root not found: {idf_root}")
    if not (project_dir / "CMakeLists.txt").exists():
        raise RuntimeError(f"ESP-IDF project not found: {project_dir}")
    env_values = read_env_file(config_file)
    sync_local_config_to_sdkconfig(env_values, sdkconfig_file, sdkconfig_defaults)
    validate_runtime_config(sdkconfig_file)

    do_build, do_flash, do_monitor = resolve_actions(args)
    port = args.port
    if (do_flash or do_monitor) and not port:
        port = auto_detect_port()
        if not port:
            raise RuntimeError("No matching serial device found under /dev/cu.usbmodem*")

    esp_python = select_python(args.esp_python)
    run_env = dict(os.environ)
    run_env["PATH"] = f"{Path(esp_python).parent}{os.pathsep}{run_env.get('PATH', '')}"

    print_header(args, idf_root, project_dir, build_dir, config_file, sdkconfig_file, port, esp_python, do_build, do_flash, do_monitor)
    if args.clean:
        code = idf_cmd(idf_root, project_dir, build_dir, sdkconfig_file, sdkconfig_defaults, run_env, ["fullclean"])
        if code != 0:
            return code
    if args.set_target:
        code = idf_cmd(idf_root, project_dir, build_dir, sdkconfig_file, sdkconfig_defaults, run_env, ["set-target", args.target])
        if code != 0:
            return code
    if do_build:
        code = idf_cmd(idf_root, project_dir, build_dir, sdkconfig_file, sdkconfig_defaults, run_env, ["build"])
        if code != 0:
            return code
    if do_flash and args.erase_flash:
        code = idf_cmd(idf_root, project_dir, build_dir, sdkconfig_file, sdkconfig_defaults, run_env, ["-p", port, "erase-flash"])
        if code != 0:
            return code
    if do_flash:
        code = idf_cmd(idf_root, project_dir, build_dir, sdkconfig_file, sdkconfig_defaults, run_env, ["-p", port, "flash"])
        if code != 0:
            return code
    if do_monitor:
        return idf_cmd(idf_root, project_dir, build_dir, sdkconfig_file, sdkconfig_defaults, run_env, ["-p", port, "monitor"])
    return 0


def resolve_actions(args: argparse.Namespace) -> tuple[bool, bool, bool]:
    """解析构建、烧录和监看动作。"""

    if args.build_only:
        return True, False, False
    if args.flash_only:
        return False, True, False
    if args.monitor_only:
        return False, False, True
    return True, True, True


def quote_sdkconfig(value: str) -> str:
    """转义 sdkconfig 字符串值。"""

    return value.replace("\\", "\\\\").replace('"', '\\"')


def upsert_line(lines: list[str], key: str, value: str) -> None:
    """更新 sdkconfig 单行。"""

    prefix = f"{key}="
    line = f'{key}="{quote_sdkconfig(value)}"'
    for index, existing in enumerate(lines):
        if existing.startswith(prefix):
            lines[index] = line
            return
    lines.append(line)


def upsert_int(lines: list[str], key: str, value: str) -> None:
    """更新 sdkconfig 整数值。"""

    prefix = f"{key}="
    line = f"{key}={value}"
    for index, existing in enumerate(lines):
        if existing.startswith(prefix):
            lines[index] = line
            return
    lines.append(line)


def upsert_bool(lines: list[str], key: str, value: str) -> None:
    """更新 sdkconfig 布尔值。"""

    normalized = value.lower() in {"1", "y", "yes", "true", "on"}
    line = f"{key}=y" if normalized else f"# {key} is not set"
    for index, existing in enumerate(lines):
        if existing.startswith(f"{key}=") or existing == f"# {key} is not set":
            lines[index] = line
            return
    lines.append(line)


def sync_local_config_to_sdkconfig(values: dict[str, str], sdkconfig_file: Path, defaults_file: Path) -> None:
    """把业务眼镜配置写入 sdkconfig.local。"""

    if defaults_file.exists():
        lines = defaults_file.read_text(encoding="utf-8").splitlines()
    else:
        lines = []
    upsert_line(lines, "CONFIG_GLASS_WIFI_PRIMARY_SSID", values.get("GLASS_WIFI_PRIMARY_SSID", ""))
    upsert_line(lines, "CONFIG_GLASS_WIFI_PRIMARY_PASSWORD", values.get("GLASS_WIFI_PRIMARY_PASSWORD", ""))
    upsert_line(lines, "CONFIG_GLASS_WIFI_FALLBACK_SSID", values.get("GLASS_WIFI_FALLBACK_SSID", ""))
    upsert_line(lines, "CONFIG_GLASS_WIFI_FALLBACK_PASSWORD", values.get("GLASS_WIFI_FALLBACK_PASSWORD", ""))
    upsert_line(lines, "CONFIG_GLASS_SERVER_WS_URI", values.get("GLASS_SERVER_WS_URI", ""))
    upsert_line(lines, "CONFIG_GLASS_DEVICE_ID", values.get("GLASS_DEVICE_ID", ""))
    upsert_line(lines, "CONFIG_GLASS_PAIR_TOKEN", values.get("GLASS_PAIR_TOKEN", ""))
    upsert_line(lines, "CONFIG_GLASS_FIRMWARE_VERSION", values.get("GLASS_FIRMWARE_VERSION", "0.1.0"))
    upsert_int(lines, "CONFIG_GLASS_HEARTBEAT_INTERVAL_MS", values.get("GLASS_HEARTBEAT_INTERVAL_MS", "5000"))
    upsert_bool(lines, "CONFIG_GLASS_ENABLE_WAKENET_TEST_APP", values.get("GLASS_ENABLE_WAKENET_TEST_APP", "0"))
    sdkconfig_file.parent.mkdir(parents=True, exist_ok=True)
    sdkconfig_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_sdkconfig_value(path: Path, key: str) -> str:
    """读取 sdkconfig 中的指定值。"""

    prefix = f"{key}="
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith(prefix):
            continue
        value = line.split("=", 1)[1]
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        return value
    return ""


def validate_runtime_config(sdkconfig_file: Path) -> None:
    """校验眼镜运行时必要配置。"""

    checks = {
        "CONFIG_GLASS_WIFI_PRIMARY_SSID": "Wi-Fi SSID 为空，当前固件无法联网",
        "CONFIG_GLASS_SERVER_WS_URI": "服务端地址为空，当前固件无法连接服务端",
        "CONFIG_GLASS_DEVICE_ID": "设备编号为空，当前固件无法注册",
        "CONFIG_GLASS_PAIR_TOKEN": "配对令牌为空，当前固件无法通过校验",
    }
    for key, message in checks.items():
        if not read_sdkconfig_value(sdkconfig_file, key):
            raise RuntimeError(f"{message}: {sdkconfig_file}")
    interval = read_sdkconfig_value(sdkconfig_file, "CONFIG_GLASS_HEARTBEAT_INTERVAL_MS")
    if not interval.isdigit() or int(interval) <= 0:
        raise RuntimeError(f"CONFIG_GLASS_HEARTBEAT_INTERVAL_MS 非法: {interval}")


def auto_detect_port() -> str:
    """自动选择第一个 ESP32-S3 串口。"""

    ports = sorted(Path("/dev").glob("cu.usbmodem*"))
    return str(ports[0]) if ports else ""


def select_python(preferred: str) -> str:
    """选择 ESP-IDF 使用的 Python。"""

    if preferred and Path(preferred).exists():
        return preferred
    default = Path("/opt/miniconda3/bin/python3")
    if default.exists():
        return str(default)
    python3 = shutil.which("python3")
    if python3:
        return python3
    raise RuntimeError("No usable Python found for ESP-IDF")


def idf_cmd(
    idf_root: Path,
    project_dir: Path,
    build_dir: Path,
    sdkconfig_file: Path,
    defaults_file: Path,
    env: dict[str, str],
    args: list[str],
) -> int:
    """在 ESP-IDF 环境中执行 idf.py。"""

    idf_export = idf_root / "export.sh"
    command = (
        f"source {sh_quote(str(idf_export))} >/dev/null && "
        "idf.py "
        f"-B {sh_quote(str(build_dir))} "
        f"-DSDKCONFIG={sh_quote(str(sdkconfig_file))} "
        f"-DSDKCONFIG_DEFAULTS={sh_quote(str(defaults_file))} "
        + " ".join(sh_quote(item) for item in args)
    )
    return subprocess.run(["bash", "-lc", command], cwd=str(project_dir), env=env, check=False).returncode


def sh_quote(value: str) -> str:
    """返回 shell 安全字符串。"""

    import shlex

    return shlex.quote(value)


def print_header(
    args: argparse.Namespace,
    idf_root: Path,
    project_dir: Path,
    build_dir: Path,
    config_file: Path,
    sdkconfig_file: Path,
    port: str,
    esp_python: str,
    do_build: bool,
    do_flash: bool,
    do_monitor: bool,
) -> None:
    """打印眼镜端构建摘要。"""

    print("========================================")
    print(" OpenAI Glass Build + Flash + Monitor")
    print("========================================")
    print(f"IDF root    : {idf_root}")
    print(f"Project dir : {project_dir}")
    print(f"Target      : {args.target}")
    print(f"Port        : {port or '<none>'}")
    print(f"Build dir   : {build_dir}")
    print(f"SDKCONFIG   : {sdkconfig_file}")
    print(f"Local config: {config_file}")
    print(f"Baud        : {args.baud}")
    print(f"Python      : {esp_python}")
    print(f"Build       : {int(do_build)}")
    print(f"Flash       : {int(do_flash)}")
    print(f"Monitor     : {int(do_monitor)}")
    print(f"Set target  : {int(args.set_target)}")
    print(f"Fullclean   : {int(args.clean)}")
    print(f"Erase flash : {int(args.erase_flash)}")
    print()
