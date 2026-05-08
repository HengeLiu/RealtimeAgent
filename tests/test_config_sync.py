from __future__ import annotations

import json
import subprocess
from pathlib import Path


AUDIO_ROOT = Path(__file__).resolve().parents[1]


def test_config_sync_generates_developer_files(tmp_path) -> None:
    """测试目标：确认 `audio-chat.config.sync` 能生成开发者本地配置。

    测试方法：把 app-root 指向临时目录，指定 output-dir 后执行同步命令。
    预期结果：生成 server、phone mock、glass playback 三类配置和 sync-result.json。
    """

    app_root = tmp_path / "basic-app"
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

