from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml

from realtime_agent_esp32_s3.esp32_aec import Esp32S3EndpointConfig


AUDIO_ROOT = Path(__file__).resolve().parents[4]


def _support_types(config: dict) -> set[str]:
    """提取结构化 supports 中的能力类型。"""

    supports = config.get("supports") or {}
    sensors = {f"sensor.{item['type']}" for item in supports.get("sensors", [])}
    actuators = {f"actuator.{item['type']}" for item in supports.get("actuators", [])}
    return sensors | actuators


def test_endpoint_config_sync_generates_all_reference_endpoint_configs(tmp_path: Path) -> None:
    """测试目标：验证 config sync 能生成多端参考配置。

    测试方法：执行 `realtime-agent.config.sync`，指定统一 server_url、user_id 和静态 token。
    预期结果：server、glass playback、python phone mock、browser-glass、iOS SDK Demo、ESP32-S3
    配置全部生成，且共享同一组 server_url、user_id 和鉴权 token。
    """

    output_dir = tmp_path / "generated"
    completed = subprocess.run(
        [
            "uv",
            "run",
            "realtime-agent.config.sync",
            "--app-root",
            str(tmp_path / "app"),
            "--output-dir",
            str(output_dir),
            "--server-url",
            "http://10.0.0.2:8765",
            "--user-id",
            "user-sync",
            "--auth-token",
            "token-sync",
        ],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

    report = json.loads((output_dir / "sync-result.json").read_text(encoding="utf-8"))
    assert report["ok"] is True
    expected_keys = {"server", "phone_mock", "glass_playback", "web_glass", "ios_device_demo", "esp32_s3"}
    assert expected_keys.issubset(report["files"])
    for path in report["files"].values():
        assert Path(path).exists()

    phone = yaml.safe_load(Path(report["files"]["phone_mock"]).read_text(encoding="utf-8"))
    glass = yaml.safe_load(Path(report["files"]["glass_playback"]).read_text(encoding="utf-8"))
    web = yaml.safe_load(Path(report["files"]["web_glass"]).read_text(encoding="utf-8"))
    ios = json.loads(Path(report["files"]["ios_device_demo"]).read_text(encoding="utf-8"))
    esp32 = Path(report["files"]["esp32_s3"]).read_text(encoding="utf-8")

    for config in (phone, glass, web, ios):
        assert config["server_url"] == "http://10.0.0.2:8765"
        assert config["user_id"] == "user-sync"
        assert config["auth"]["mode"] == "static_token"
        assert config["auth"]["token"] == "token-sync"
    assert ios["audio_input"]["enabled"] is True
    assert ios["camera"]["enabled"] is True
    assert ios["camera"]["modes"] == ["single"]
    assert ios["speaker"]["enabled"] is True
    assert "supports" not in ios
    assert "REALTIME_AGENT_SERVER_URL=http://10.0.0.2:8765" in esp32
    assert "REALTIME_AGENT_CONTROL_WS_URL=ws://10.0.0.2:8765/ws/control" in esp32
    assert "REALTIME_AGENT_STREAM_WS_URL=ws://10.0.0.2:8765/ws/stream" in esp32
    assert "REALTIME_AGENT_USER_ID=user-sync" in esp32
    assert "REALTIME_AGENT_AUTH_MODE=static_token" in esp32
    assert "REALTIME_AGENT_AUTH_TOKEN=token-sync" in esp32
    assert "REALTIME_AGENT_AUDIO_SAMPLE_RATE=16000" in esp32
    assert 'REALTIME_AGENT_STREAMS_PRODUCE=["sensor.mic","sensor.rgb"]' in esp32
    assert 'REALTIME_AGENT_STREAMS_CONSUME=["actuator.speaker"]' in esp32
    assert "REALTIME_AGENT_SUPPORTS=" in esp32
    assert '"type":"rgb"' in esp32
    assert _support_types(phone) >= {"sensor.rgb", "actuator.vibrator"}
    assert _support_types(web) >= {"sensor.rgb", "actuator.vibrator"}
    esp32_config = Esp32S3EndpointConfig.from_env_file(report["files"]["esp32_s3"])
    assert esp32_config.server_url == "http://10.0.0.2:8765"
    assert esp32_config.user_id == "user-sync"
    assert esp32_config.auth_payload() == {"mode": "static_token", "token": "token-sync"}


def test_endpoint_config_sync_uses_distinct_device_ids_under_same_user(tmp_path: Path) -> None:
    """测试目标：验证同步配置不依赖固定 glass / phone 角色，也不复用 device_id。

    测试方法：读取生成的各端配置，比较 user_id 和 device_id。
    预期结果：所有参考端侧共享同一 user_id，但 device_id 各自唯一，路由由
    event/route 决定。
    """

    output_dir = tmp_path / "generated"
    subprocess.run(
        ["uv", "run", "realtime-agent.config.sync", "--output-dir", str(output_dir), "--user-id", "user-shared"],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    report = json.loads((output_dir / "sync-result.json").read_text(encoding="utf-8"))
    phone = yaml.safe_load(Path(report["files"]["phone_mock"]).read_text(encoding="utf-8"))
    glass = yaml.safe_load(Path(report["files"]["glass_playback"]).read_text(encoding="utf-8"))
    web = yaml.safe_load(Path(report["files"]["web_glass"]).read_text(encoding="utf-8"))
    ios = json.loads(Path(report["files"]["ios_device_demo"]).read_text(encoding="utf-8"))
    esp32 = Esp32S3EndpointConfig.from_env_file(report["files"]["esp32_s3"])

    configs = [phone, glass, web, ios, {"user_id": esp32.user_id, "device_id": esp32.device_id}]
    assert {config["user_id"] for config in configs} == {"user-shared"}
    assert len({config["device_id"] for config in configs}) == len(configs)
    assert "phone.task.find_object_phone_task" not in phone.get("properties", {})
    assert _support_types(phone) >= {"sensor.rgb", "actuator.vibrator"}
    assert esp32.device_id == "dev-esp32-s3-001"
    assert ios["device_id"] == "dev-device-demo-ios-001"
    assert ios["audio_input"]["enabled"] is True
    assert ios["camera"]["enabled"] is True
    assert ios["camera"]["modes"] == ["single"]
    assert ios["speaker"]["enabled"] is True
    assert "supports" not in ios
    assert "routes" not in ios


def test_endpoint_config_sync_can_emit_signed_token_hint_for_ios(tmp_path: Path) -> None:
    """测试目标：验证 signed_token 模式下 iOS 配置不会静默退回 disabled。

    测试方法：执行 config sync 并显式指定 `--auth-mode signed_token`，不提供实际 token。
    预期结果：生成的 iOS SDK Demo 配置保留 signed_token 模式，并写入开发者生成 token 的提示。
    """

    output_dir = tmp_path / "generated"
    subprocess.run(
        ["uv", "run", "realtime-agent.config.sync", "--output-dir", str(output_dir), "--auth-mode", "signed_token"],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    report = json.loads((output_dir / "sync-result.json").read_text(encoding="utf-8"))
    ios = json.loads(Path(report["files"]["ios_device_demo"]).read_text(encoding="utf-8"))

    assert ios["auth"]["mode"] == "signed_token"
    assert "generate signed_token" in ios["auth"]["hint"]
