from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path
from typing import Any


def config(argv: list[str] | None = None) -> None:
    """同步 ESP32-S3 参考端本地 env 配置。

    主要逻辑：
    1. 默认读取 `realtime-agent.config.sync` 生成的 `esp32-s3.local.env`。
    2. 写入 `examples/for-blind-app/devices/native-esp32-glass/local.env`，供固件或 bridge 读取。
    3. 源文件缺失时给出应先运行的 `config.sync` 命令。

    参数：`argv` 为命令行参数。
    返回值：无。
    异常情况：源配置不存在或输出目录不可写时抛出明确异常。
    """

    parser = argparse.ArgumentParser(prog="realtime-agent.esp32.config", description="同步 ESP32-S3 参考端配置")
    parser.add_argument("--source", default="examples/for-blind-app/agent-server/config/generated/esp32-s3.local.env", help="源 env 文件")
    parser.add_argument("--output", default="examples/for-blind-app/devices/native-esp32-glass/local.env", help="输出 env 文件")
    parser.add_argument("--print-path", action="store_true", help="只打印输出路径")
    args = parser.parse_args(argv)

    source = _resolve_audio_root_path(args.source)
    output = _resolve_audio_root_path(args.output)
    if not source.exists():
        raise FileNotFoundError(
            f"ESP32 config source not found: {source}. "
            "请先运行 realtime-agent.config.sync --output-dir examples/for-blind-app/agent-server/config/generated"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, output)
    if args.print_path:
        print(output)
    else:
        print(f"esp32 config written: {output}")


def build(argv: list[str] | None = None) -> None:
    """构建 ESP32-S3 参考固件。"""

    _run_idf_action("realtime-agent.esp32.build", "build", argv)


def flash(argv: list[str] | None = None) -> None:
    """烧录 ESP32-S3 参考固件。"""

    _run_idf_action("realtime-agent.esp32.flash", "flash", argv)


def monitor(argv: list[str] | None = None) -> None:
    """监看 ESP32-S3 串口日志。"""

    _run_idf_action("realtime-agent.esp32.monitor", "monitor", argv)


def _run_idf_action(prog: str, action: str, argv: list[str] | None) -> None:
    """执行 ESP-IDF 动作。

    主要逻辑：参考端目前只冻结命令入口和诊断行为；真机命令要求开发者显式提供
    可用 ESP-IDF 工程。缺少工程或 `idf.py` 时直接失败，不做假成功。
    """

    parser = argparse.ArgumentParser(prog=prog, description=f"执行 ESP32-S3 {action} 动作")
    parser.add_argument("--project-dir", default="examples/for-blind-app/devices/native-esp32-glass/firmware", help="ESP-IDF 工程目录")
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
    manifest_check = _esp32_project_manifest_check(project_dir)
    if not manifest_check["ok"]:
        raise RuntimeError("ESP32-S3 project manifest check failed: " + "; ".join(manifest_check["errors"]))
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


def _esp32_project_manifest_check(project_dir: Path) -> dict[str, Any]:
    """检查 ESP32-S3 参考工程的最小文件集合。

    功能：
    1. 给 `realtime-agent.esp32.*` 命令和 package-check 复用同一套工程文件校验。
    2. 在没有 ESP-IDF 或真机时，也能先确认参考端目录结构没有缺文件。

    参数：
    1. `project_dir`：ESP-IDF 工程目录。

    返回值：
    1. 结构化检查结果，包含 `ok`、`files` 和 `errors`。

    异常情况：
    1. 不抛出异常；调用方决定是否把错误升级为命令失败。
    """

    required = [
        "CMakeLists.txt",
        "main/CMakeLists.txt",
        "main/idf_component.yml",
        "main/realtime_agent_reference_main.c",
        "sdkconfig.defaults",
    ]
    files = {name: str(project_dir / name) for name in required}
    errors = [f"missing ESP32 project file: {name}" for name in required if not (project_dir / name).exists()]
    manifest = project_dir / "main/idf_component.yml"
    if manifest.exists():
        manifest_text = manifest.read_text(encoding="utf-8")
        if "esp_websocket_client" not in manifest_text:
            errors.append("idf_component.yml missing dependency token: esp_websocket_client")
    main_cmake = project_dir / "main/CMakeLists.txt"
    if main_cmake.exists() and "json" not in main_cmake.read_text(encoding="utf-8"):
        errors.append("main/CMakeLists.txt missing component token: json")
    return {"ok": not errors, "files": files, "errors": errors}


def _resolve_audio_root_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    if path.exists():
        return path.resolve()
    audio_root = Path(__file__).resolve().parents[3]
    return (audio_root / path).resolve()
