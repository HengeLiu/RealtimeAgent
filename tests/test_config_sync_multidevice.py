from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml


AUDIO_ROOT = Path(__file__).resolve().parents[1]


def test_config_sync_updates_server_and_all_endpoint_configs(tmp_path: Path) -> None:
    """测试目标：验证 config.sync 同步 server、playback、phone、web、iOS、ESP32 六类配置。

    测试方法：指定统一 server_url、user_id 和静态 token 后执行同步命令。
    预期结果：server YAML 的 public_url/auth 与所有端侧配置一致，端侧 device_id 不冲突。
    """

    output_dir = tmp_path / "generated"
    completed = subprocess.run(
        [
            "uv",
            "run",
            "audio-chat.config.sync",
            "--output-dir",
            str(output_dir),
            "--server-url",
            "http://10.1.2.3:8765",
            "--user-id",
            "user-device-api-upgrade",
            "--auth-token",
            "token-device-api-upgrade",
        ],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

    report = json.loads((output_dir / "sync-result.json").read_text(encoding="utf-8"))
    server = yaml.safe_load(Path(report["files"]["server"]).read_text(encoding="utf-8"))
    endpoint_configs = [
        yaml.safe_load(Path(report["files"]["phone_mock"]).read_text(encoding="utf-8")),
        yaml.safe_load(Path(report["files"]["glass_playback"]).read_text(encoding="utf-8")),
        yaml.safe_load(Path(report["files"]["web_glass"]).read_text(encoding="utf-8")),
        json.loads(Path(report["files"]["ios_phone"]).read_text(encoding="utf-8")),
    ]
    esp32_env = Path(report["files"]["esp32_s3"]).read_text(encoding="utf-8")

    assert server["server"]["public_url"] == "http://10.1.2.3:8765"
    assert server["auth"]["mode"] == "static_token"
    assert set(server["auth"]["device_tokens"]) == {
        "dev-python-playback-001",
        "dev-python-phone-001",
        "dev-browser-glass-001",
        "dev-ios-phone-001",
        "dev-esp32-s3-001",
    }
    assert {config["user_id"] for config in endpoint_configs} == {"user-device-api-upgrade"}
    assert len({config["device_id"] for config in endpoint_configs}) == len(endpoint_configs)
    for config in endpoint_configs:
        assert config["server_url"] == "http://10.1.2.3:8765"
        assert config["auth"] == {"mode": "static_token", "token": "token-device-api-upgrade"}
        assert config["supports"]
    assert "AUDIO_CHAT_SERVER_URL=http://10.1.2.3:8765" in esp32_env
    assert "AUDIO_CHAT_AUTH_MODE=static_token" in esp32_env
    assert "AUDIO_CHAT_AUTH_TOKEN=token-device-api-upgrade" in esp32_env
    assert "AUDIO_CHAT_SUPPORTS=" in esp32_env


def test_esp32_config_command_copies_generated_env(tmp_path: Path) -> None:
    """测试目标：验证 ESP32 config 命令能把同步产物复制到参考端侧目录。

    测试方法：先运行 config.sync 到临时目录，再调用 `audio-chat.esp32.config`。
    预期结果：输出 env 文件存在，且内容保持 server_url/user_id/device_id。
    """

    generated = tmp_path / "generated"
    subprocess.run(
        ["uv", "run", "audio-chat.config.sync", "--output-dir", str(generated), "--user-id", "user-esp32"],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    output = tmp_path / "local.env"
    completed = subprocess.run(
        [
            "uv",
            "run",
            "audio-chat.esp32.config",
            "--source",
            str(generated / "esp32-s3.local.env"),
            "--output",
            str(output),
            "--print-path",
        ],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    text = output.read_text(encoding="utf-8")
    assert "AUDIO_CHAT_USER_ID=user-esp32" in text
    assert "AUDIO_CHAT_DEVICE_ID=dev-esp32-s3-001" in text
