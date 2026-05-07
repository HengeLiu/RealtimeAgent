from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


def config(argv: list[str] | None = None) -> None:
    """同步 ESP32-S3 参考端本地 env 配置。

    主要逻辑：
    1. 默认读取 `audio-chat.config.sync` 生成的 `esp32-s3.local.env`。
    2. 写入 `endpoints/esp32-s3/local.env`，供固件或 bridge 读取。
    3. 源文件缺失时给出应先运行的 `config.sync` 命令。

    参数：`argv` 为命令行参数。
    返回值：无。
    异常情况：源配置不存在或输出目录不可写时抛出明确异常。
    """

    parser = argparse.ArgumentParser(prog="audio-chat.esp32.config", description="同步 ESP32-S3 参考端配置")
    parser.add_argument("--source", default="examples/basic-app/config/generated/esp32-s3.local.env", help="源 env 文件")
    parser.add_argument("--output", default="endpoints/esp32-s3/local.env", help="输出 env 文件")
    parser.add_argument("--print-path", action="store_true", help="只打印输出路径")
    args = parser.parse_args(argv)

    source = _resolve_audio_root_path(args.source)
    output = _resolve_audio_root_path(args.output)
    if not source.exists():
        raise FileNotFoundError(
            f"ESP32 config source not found: {source}. "
            "请先运行 audio-chat.config.sync --output-dir examples/basic-app/config/generated"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, output)
    if args.print_path:
        print(output)
    else:
        print(f"esp32 config written: {output}")


def build(argv: list[str] | None = None) -> None:
    """构建 ESP32-S3 参考固件。"""

    _run_idf_action("audio-chat.esp32.build", "build", argv)


def flash(argv: list[str] | None = None) -> None:
    """烧录 ESP32-S3 参考固件。"""

    _run_idf_action("audio-chat.esp32.flash", "flash", argv)


def monitor(argv: list[str] | None = None) -> None:
    """监看 ESP32-S3 串口日志。"""

    _run_idf_action("audio-chat.esp32.monitor", "monitor", argv)


def _run_idf_action(prog: str, action: str, argv: list[str] | None) -> None:
    """执行 ESP-IDF 动作。

    主要逻辑：参考端目前只冻结命令入口和诊断行为；真机命令要求开发者显式提供
    可用 ESP-IDF 工程。缺少工程或 `idf.py` 时直接失败，不做假成功。
    """

    parser = argparse.ArgumentParser(prog=prog, description=f"执行 ESP32-S3 {action} 动作")
    parser.add_argument("--project-dir", default="endpoints/esp32-s3/firmware", help="ESP-IDF 工程目录")
    parser.add_argument("--port", default="", help="串口端口，flash/monitor 时使用")
    parser.add_argument("--idf-py", default="idf.py", help="idf.py 命令路径")
    parser.add_argument("--dry-run", action="store_true", help="只输出诊断，不执行 idf.py")
    args = parser.parse_args(argv)

    project_dir = _resolve_audio_root_path(args.project_dir)
    if not (project_dir / "CMakeLists.txt").exists():
        raise RuntimeError(
            f"ESP-IDF project not found: {project_dir}. "
            "当前仓库只提供 ESP32-S3 参考协议和配置；真机 smoke 需要指定实际固件工程 --project-dir"
        )
    command = [args.idf_py]
    if action in {"flash", "monitor"} and args.port:
        command.extend(["-p", args.port])
    command.append(action)
    if args.dry_run:
        print({"project_dir": str(project_dir), "command": command})
        return
    if shutil.which(args.idf_py) is None and not Path(args.idf_py).exists():
        raise RuntimeError("idf.py not found；请先安装 ESP-IDF，或用 --idf-py 指定完整路径")
    subprocess.run(command, cwd=project_dir, check=True)


def _resolve_audio_root_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    if path.exists():
        return path.resolve()
    audio_root = Path(__file__).resolve().parents[3]
    return (audio_root / path).resolve()
