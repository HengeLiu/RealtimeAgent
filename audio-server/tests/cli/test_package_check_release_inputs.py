from __future__ import annotations

import json
import subprocess
from pathlib import Path


AUDIO_ROOT = Path(__file__).resolve().parents[3]


def test_package_check_covers_current_entry_points(tmp_path: Path) -> None:
    """测试目标：确认 package-check 覆盖当前 SDK CLI。

    测试方法：运行 package-check 并读取报告中的 entry point 数量。
    预期结果：报告通过，且脚本数量覆盖 config/server/playback/web/iOS/ESP32/dev/sdk。
    """

    report = tmp_path / "package-check.json"
    completed = subprocess.run(
        ["uv", "run", "audio-chat.sdk.package-check", "--report", str(report)],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, f"stdout={completed.stdout}\nstderr={completed.stderr}"
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["ok"] is True
    assert data["script_count"] >= 16
