from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


AUDIO_ROOT = Path(__file__).resolve().parents[3]


def test_release_package_check_reports_release_candidate_gate(tmp_path: Path) -> None:
    """测试目标：确认发布候选 package-check 输出完整 release candidate 报告。

    测试方法：运行 `realtime-agent.sdk.package-check`，读取报告中的版本、wheel、
    端侧源码和边界检查结果。
    预期结果：报告为通过状态，版本带 rc 标识，wheel 安装导入和源码边界检查都通过。
    """

    report = tmp_path / "package-check.json"
    completed = subprocess.run(
        ["uv", "run", "realtime-agent.sdk.package-check", "--report", str(report)],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, f"stdout={completed.stdout}\nstderr={completed.stderr}"
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["ok"] is True
    assert data["package"]["release_candidate"] is True
    assert data["package"]["version"].endswith("rc1")
    assert data["checks"]["release_candidate"]["ok"] is True
    assert data["checks"]["wheel_install"]["ok"] is True
    assert data["checks"]["wheel_contents"]["ok"] is True
    assert data["checks"]["endpoint_sources"]["ok"] is True
    assert data["checks"]["source_boundary"]["ok"] is True


def test_for_blind_app_can_be_copied_to_temp_project_and_generate_endpoint_configs(tmp_path: Path) -> None:
    """测试目标：验证发布候选能支撑新项目复制精简后的 for-blind-app。

    测试方法：把 `examples/for-blind-app` 复制到临时目录，用当前 SDK 命令生成端侧配置。
    预期结果：配置同步命令成功，并生成 glass playback 配置。
    """

    app_copy = tmp_path / "for-blind-app"
    shutil.copytree(AUDIO_ROOT / "examples" / "for-blind-app", app_copy, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    output_dir = tmp_path / "generated"

    app_server = app_copy / "agent-server"
    completed = subprocess.run(
        [
            "uv",
            "run",
            "realtime-agent.config.sync",
            "--app-root",
            str(app_server),
            "--server-config",
            str(app_server / "server.yaml"),
            "--output-dir",
            str(output_dir),
        ],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, f"stdout={completed.stdout}\nstderr={completed.stderr}"
    result = json.loads(completed.stdout)
    assert result["ok"] is True
    assert Path(result["files"]["glass_playback"]).exists()
