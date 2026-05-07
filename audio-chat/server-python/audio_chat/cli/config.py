from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def sync(argv: list[str] | None = None) -> None:
    """同步本地开发配置。

    主要逻辑：
    1. 读取 app-root 和示例配置路径。
    2. 在 app-root/config/generated 下生成 server、phone mock 和 playback 配置。
    3. 写入 sync-result.json，供验收脚本确认命令真实产出文件。

    参数：`argv` 为命令行参数。
    返回值：无。
    异常情况：示例配置不存在或输出目录不可写时抛出异常。
    """

    parser = argparse.ArgumentParser(prog="audio-chat.config.sync", description="同步 audio-chat 本地开发配置")
    parser.add_argument("--app-root", default="examples/basic-app", help="业务应用根目录")
    parser.add_argument("--server-config", default="examples/minimal/server.yaml", help="源 server YAML")
    parser.add_argument("--playback-config", default="examples/minimal/playback.yaml", help="源 playback YAML")
    parser.add_argument("--output-dir", default="", help="配置输出目录，默认 app-root/config/generated")
    args = parser.parse_args(argv)

    app_root = Path(args.app_root)
    output_dir = Path(args.output_dir) if args.output_dir else app_root / "config" / "generated"
    output_dir.mkdir(parents=True, exist_ok=True)

    server_config = _resolve_input(args.server_config)
    playback_config = _resolve_input(args.playback_config)
    server_target = output_dir / "server.local.yaml"
    glass_target = output_dir / "glass.playback.yaml"
    phone_target = output_dir / "phone.mock.yaml"
    shutil.copyfile(server_config, server_target)
    shutil.copyfile(playback_config, glass_target)
    phone_target.write_text(
        "mode: python-mock\n"
        "server_url: http://127.0.0.1:8765\n"
        "user_id: user-playback-001\n"
        "device_id: dev-python-phone-mock-001\n",
        encoding="utf-8",
    )
    report = {
        "ok": True,
        "app_root": str(app_root),
        "output_dir": str(output_dir),
        "files": {
            "server": str(server_target),
            "phone_mock": str(phone_target),
            "glass_playback": str(glass_target),
        },
    }
    report_path = output_dir / "sync-result.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def _resolve_input(raw: str) -> Path:
    path = Path(raw)
    if path.exists():
        return path
    audio_root = Path(__file__).resolve().parents[3]
    candidate = audio_root / raw
    if candidate.exists():
        return candidate
    raise FileNotFoundError(raw)

