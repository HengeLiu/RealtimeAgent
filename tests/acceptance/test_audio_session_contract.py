from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from audio_chat.audio_pipeline import AudioPipeline
from audio_chat.app import AudioChatApp, AudioChatConfig


ROOT = Path(__file__).resolve().parents[2]


def test_audio_session_lifecycle_lane_is_registered() -> None:
    """测试目标：确认 A 线有独立自动验收入口。

    测试方法：解析 `scripts/acceptance_check.py --help` 输出。
    预期结果：help 中包含 `audio-session-lifecycle`。
    """

    completed = subprocess.run(
        [sys.executable, "scripts/acceptance_check.py", "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "audio-session-lifecycle" in completed.stdout


def test_audio_pipeline_contract_exposes_processors_and_preflight_status(tmp_path) -> None:
    """测试目标：冻结 A 线对 Audio Pipeline 和 preflight 的公开契约。

    测试方法：检查 App 中的 pipeline 处理器列表，并运行 preflight 写 JSON 报告。
    预期结果：处理器不止格式校验；报告明确 resample、volume probe 和 VAD 状态。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))

    assert isinstance(app.audio_pipeline, AudioPipeline)
    assert app.audio_pipeline.diagnostics_summary()["processors"] == [
        "format_validator",
        "pcm16_resampler",
        "volume_probe",
        "quality_vad_probe",
    ]

    report = tmp_path / "preflight.json"
    completed = subprocess.run(
        [
            "uv",
            "run",
            "audio-chat.dev.preflight",
            "--config",
            "app-examples/basic-app/server.yaml",
            "--report",
            str(report),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    audio_check = next(check for check in data["checks"] if check["name"] == "audio_pipeline")
    assert audio_check["ok"] is True
    assert audio_check["resample"]["status"] == "enabled"
    assert audio_check["volume_probe"]["enabled"] is True
    assert audio_check["volume_probe"]["changes_audio"] is False
    assert audio_check["vad"]["status"] == "diagnostic"
    assert audio_check["vad"]["owns_turn_boundary"] is False
