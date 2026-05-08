from __future__ import annotations

import json
import subprocess
from pathlib import Path

from audio_chat.app import AudioChatApp
from audio_chat.app_loader import load_app_config, resolve_app_launch


AUDIO_ROOT = Path(__file__).resolve().parents[1]


def test_app_name_resolves_app_directory_config_and_capabilities() -> None:
    """`app_name` should map to app-examples/<app_name>."""

    launch = resolve_app_launch("basic-app", app_root=AUDIO_ROOT / "app-examples")

    assert launch.app_name == "basic-app"
    assert launch.app_dir == (AUDIO_ROOT / "app-examples" / "basic-app").resolve()
    assert launch.config_path == launch.app_dir / "server.yaml"
    assert launch.capabilities_dir == launch.app_dir / "capabilities"


def test_app_name_loads_server_yaml_and_auto_registers_capabilities() -> None:
    """Starting by app_name should not require a manual app module."""

    config, launch = load_app_config("basic-app", app_root=AUDIO_ROOT / "app-examples")
    app = AudioChatApp(config)

    assert config.app_name == "basic-app"
    assert config.app_dir == str(launch.app_dir)
    assert config.config_path == str(launch.config_path)
    assert config.tools_discover_enabled is True
    assert config.tasks_discover_enabled is True
    assert "echo_text" in app.tool_registry.list_names()
    assert "timer" in app.task_engine.registry.list_task_types()


def test_server_start_dry_run_accepts_app_name(tmp_path) -> None:
    """CLI background launcher should preserve app_name metadata."""

    pid_file = tmp_path / "server.pid"
    log_file = tmp_path / "server.log"
    completed = subprocess.run(
        [
            "uv",
            "run",
            "audio-chat.server.start",
            "--app-name",
            "basic-app",
            "--pid-file",
            str(pid_file),
            "--log-file",
            str(log_file),
            "--dry-run",
        ],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    metadata = json.loads(pid_file.read_text(encoding="utf-8"))
    assert metadata["app_name"] == "basic-app"
    assert metadata["app_root"] == "app-examples"
