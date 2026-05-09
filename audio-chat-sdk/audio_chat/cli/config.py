from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


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
    parser.add_argument("--app-root", default="app-examples/for-blind-app", help="业务应用根目录")
    parser.add_argument("--server-config", default="app-examples/for-blind-app/server.yaml", help="源 server YAML")
    parser.add_argument("--playback-config", default="app-examples/for-blind-app/host/glass-playback/sdk-playback.yaml", help="源 playback YAML")
    parser.add_argument("--server-url", default="http://127.0.0.1:8765", help="各参考端侧使用的 server URL")
    parser.add_argument("--user-id", default="user-playback-001", help="各参考端侧使用的 user_id")
    parser.add_argument(
        "--auth-mode",
        choices=["disabled", "static_token", "signed_token"],
        default="",
        help="端侧注册鉴权模式；默认根据 --auth-token 自动选择",
    )
    parser.add_argument("--auth-token", default="", help="可选静态鉴权 token；为空时使用 disabled auth")
    parser.add_argument("--signed-token", default="", help="可选 signed_token；仅 --auth-mode signed_token 时写入")
    parser.add_argument("--output-dir", default="", help="配置输出目录，默认 app-root/config/generated")
    args = parser.parse_args(argv)

    app_root = Path(args.app_root)
    output_dir = Path(args.output_dir) if args.output_dir else app_root / "config" / "generated"
    output_dir.mkdir(parents=True, exist_ok=True)

    server_config = _resolve_input(args.server_config)
    playback_config = _resolve_input(args.playback_config)
    auth_config = _auth(args.auth_mode, args.auth_token, args.signed_token)
    server_target = output_dir / "server.local.yaml"
    glass_target = output_dir / "glass.playback.yaml"
    phone_target = output_dir / "phone.mock.yaml"
    web_target = output_dir / "browser-glass.yaml"
    ios_target = output_dir / "ios-phone.local.json"
    esp32_target = output_dir / "esp32-s3.local.env"
    server_data = _read_yaml(server_config)
    server_data.setdefault("server", {})["public_url"] = args.server_url
    server_data.setdefault("auth", {})["mode"] = auth_config["mode"]
    if args.auth_token:
        server_data.setdefault("auth", {})["device_tokens"] = {
            "dev-python-playback-001": args.auth_token,
            "dev-python-phone-001": args.auth_token,
            "dev-browser-glass-001": args.auth_token,
            "dev-ios-phone-001": args.auth_token,
            "dev-esp32-s3-001": args.auth_token,
        }
    _write_yaml(server_target, server_data)
    _write_yaml(
        glass_target,
        {
            **_read_yaml(playback_config),
            "server_url": args.server_url,
            "user_id": args.user_id,
            "device_id": "dev-python-playback-001",
            "auth": auth_config,
            "supports": _glass_supports(),
        },
    )
    _write_yaml(
        phone_target,
        {
            "mode": "register_only",
            "server_url": args.server_url,
            "user_id": args.user_id,
            "device_id": "dev-python-phone-001",
            "auth": auth_config,
            "properties": {},
            "supports": _phone_supports(),
        },
    )
    _write_yaml(
        web_target,
        {
            "server_url": args.server_url,
            "user_id": args.user_id,
            "device_id": "dev-browser-glass-001",
            "client_type": "browser-glass",
            "auth": auth_config,
            "supports": _phone_supports(),
            "audio": {"aec": "browser_webrtc", "wake_word": "manual"},
            "stream": {"sensor_mic": {"codec": "pcm16le", "sample_rate": 16000, "channels": 1, "chunk_ms": 20}},
        },
    )
    ios_target.write_text(
        json.dumps(
            {
                "server_url": args.server_url,
                "user_id": args.user_id,
                "device_id": "dev-ios-phone-001",
                "auth": auth_config,
                "protocol_version": "audio-chat.v1",
                "properties": {
                    "audio.aec": "replaceable",
                    "audio.wake_word": "manual",
                },
                "supports": _ios_supports(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    esp32_target.write_text(
        "\n".join(
            [
                f"AUDIO_CHAT_SERVER_URL={args.server_url}",
                f"AUDIO_CHAT_CONTROL_WS_URL={args.server_url.replace('http://', 'ws://').replace('https://', 'wss://')}/ws/control",
                f"AUDIO_CHAT_STREAM_WS_URL={args.server_url.replace('http://', 'ws://').replace('https://', 'wss://')}/ws/stream",
                f"AUDIO_CHAT_USER_ID={args.user_id}",
                "AUDIO_CHAT_DEVICE_ID=dev-esp32-s3-001",
                f"AUDIO_CHAT_AUTH_MODE={auth_config['mode']}",
                f"AUDIO_CHAT_AUTH_TOKEN={auth_config.get('token') or auth_config.get('signed_token') or args.auth_token}",
                "AUDIO_CHAT_WAKE_WORD_MODE=endpoint",
                "AUDIO_CHAT_AEC_MODE=endpoint",
                "AUDIO_CHAT_PLAYBACK_REFERENCE=endpoint_ring_buffer",
                "AUDIO_CHAT_AUDIO_CODEC=pcm16le",
                "AUDIO_CHAT_AUDIO_SAMPLE_RATE=16000",
                "AUDIO_CHAT_AUDIO_CHANNELS=1",
                "AUDIO_CHAT_AUDIO_CHUNK_MS=20",
                'AUDIO_CHAT_STREAMS_PRODUCE=["sensor.mic","sensor.rgb"]',
                'AUDIO_CHAT_STREAMS_CONSUME=["actuator.speaker"]',
                'AUDIO_CHAT_SUPPORTS={"sensors":[{"type":"rgb","modes":["single"],"default":{"format":"jpeg","frequency_hz":1,"sample_count":1}}]}',
                "",
            ]
        ),
        encoding="utf-8",
    )
    report = {
        "ok": True,
        "app_root": str(app_root),
        "server_url": args.server_url,
        "user_id": args.user_id,
        "output_dir": str(output_dir),
        "files": {
            "server": str(server_target),
            "phone_mock": str(phone_target),
            "glass_playback": str(glass_target),
            "web_glass": str(web_target),
            "ios_phone": str(ios_target),
            "esp32_s3": str(esp32_target),
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


def _read_yaml(path: Path) -> dict[str, Any]:
    import yaml

    return dict(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    import yaml

    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _phone_supports() -> dict[str, Any]:
    """生成 Python phone mock 和 iOS 参考端共用的设备语义能力。"""

    return {
        "sensors": [
            {"type": "rgb", "modes": ["single", "continuous"], "default": {"format": "jpeg", "frequency_hz": 1, "sample_count": 1}}
        ],
        "actuators": [
            {"type": "vibrator", "commands": ["vibrate"]}
        ],
    }


def _glass_supports() -> dict[str, Any]:
    """生成 Python glass playback 注册使用的设备语义能力。"""

    return {
        "sensors": [
            {"type": "rgb", "modes": ["single"], "default": {"format": "jpeg", "frequency_hz": 1, "sample_count": 1}}
        ]
    }


def _ios_supports() -> dict[str, Any]:
    """生成 iOS 参考端注册使用的设备语义能力。"""

    return _phone_supports()


def _auth(mode: str, token: str, signed_token: str = "") -> dict[str, str]:
    resolved_mode = mode or ("static_token" if token else "disabled")
    if resolved_mode == "static_token":
        return {"mode": "static_token", "token": token}
    if resolved_mode == "signed_token":
        data = {"mode": "signed_token"}
        if signed_token:
            data["signed_token"] = signed_token
        else:
            data["hint"] = "generate signed_token with the pairing service before registering this endpoint"
        return data
    return {"mode": "disabled"}
