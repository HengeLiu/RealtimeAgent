from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml


AUDIO_ROOT = Path(__file__).resolve().parents[1]


def test_config_sync_generates_developer_files(tmp_path) -> None:
    """测试目标：确认 `audio-chat.config.sync` 能生成开发者本地配置。

    测试方法：把 app-root 指向临时目录，指定 output-dir 后执行同步命令。
    预期结果：生成 server、phone mock、glass playback 三类配置和 sync-result.json。
    """

    app_root = tmp_path / "for-blind-app"
    output_dir = tmp_path / "generated"
    completed = subprocess.run(
        [
            "uv",
            "run",
            "audio-chat.config.sync",
            "--app-root",
            str(app_root),
            "--output-dir",
            str(output_dir),
        ],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads((output_dir / "sync-result.json").read_text(encoding="utf-8"))
    assert report["ok"] is True
    for key in ("server", "phone_mock", "glass_playback"):
        assert Path(report["files"][key]).exists()


def test_config_sync_derives_playback_runs_root_from_server_app_name(tmp_path) -> None:
    """测试目标：确认 SDK 生成端侧配置时不硬编码示例业务名称。

    测试方法：写入一个 app-name 为 custom-app 的 server.yaml，执行 config.sync。
    预期结果：生成的 glass.playback.yaml 使用 `runs/custom-app` 作为 runs_root。
    """

    app_root = tmp_path / "custom-app"
    app_root.mkdir()
    server_config = app_root / "server.yaml"
    server_config.write_text(
        """
app-name: custom-app
server:
  public_url: http://127.0.0.1:8765
""".lstrip(),
        encoding="utf-8",
    )
    output_dir = tmp_path / "generated"

    completed = subprocess.run(
        [
            "uv",
            "run",
            "audio-chat.config.sync",
            "--app-root",
            str(app_root),
            "--server-config",
            str(server_config),
            "--output-dir",
            str(output_dir),
        ],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    playback = yaml.safe_load((output_dir / "glass.playback.yaml").read_text(encoding="utf-8"))
    assert playback["runs_root"] == "runs/custom-app"
