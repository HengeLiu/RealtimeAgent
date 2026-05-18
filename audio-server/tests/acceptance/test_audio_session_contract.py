from __future__ import annotations

import json
import subprocess
from pathlib import Path

from audio_chat.audio_pipeline import AudioPipeline
from audio_chat.app import AudioChatApp, AudioChatConfig


ROOT = Path(__file__).resolve().parents[3]


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
    ]

    report = tmp_path / "preflight.json"
    completed = subprocess.run(
        [
            "uv",
            "run",
            "audio-chat.dev.preflight",
            "--config",
            "examples/for-blind-app/audio-server/server.yaml",
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
    assert audio_check["vad"]["status"] == "server"
    assert audio_check["vad"]["owns_turn_boundary"] is True
