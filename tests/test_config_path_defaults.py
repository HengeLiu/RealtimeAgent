from __future__ import annotations

from pathlib import Path

from audio_chat.config import load_yaml_config


def test_yaml_config_derives_runtime_paths_from_app_name(tmp_path: Path) -> None:
    """测试目标：确认开发者不需要分别配置每个运行产物路径。

    测试方法：写入只包含 app-name 和少量功能开关的 server.yaml，并读取配置对象。
    预期结果：用户消息、资产、记忆和 preflight 报告路径都从 app-name 统一派生。
    """

    config_path = tmp_path / "server.yaml"
    config_path.write_text(
        """
app-name: demo-app
memory:
  enabled: true
dev_checks:
  run_contract_tests: true
""".lstrip(),
        encoding="utf-8",
    )

    config = load_yaml_config(config_path)

    assert config.paths.runtime_root == "runs/demo-app"
    assert config.observability.runs_root == "runs/demo-app"
    assert config.user.message_store["root"] == "runs/demo-app/users"
    assert config.asset.root == "runs/demo-app/assets"
    assert config.memory.path == "runs/demo-app/memory"
    assert config.dev_checks.report_path == "runs/demo-app/preflight.json"
    assert config.dev_checks.contract_tests_path == "testdata/contracts"


def test_yaml_config_keeps_explicit_sub_path_overrides(tmp_path: Path) -> None:
    """测试目标：确认统一路径方案不破坏老配置和特殊目录覆盖。

    测试方法：同时配置 paths.runtime_root 和若干显式子路径后加载配置。
    预期结果：未配置的路径按 runtime_root 派生，已显式配置的路径保持原值。
    """

    config_path = tmp_path / "server.yaml"
    config_path.write_text(
        """
app-name: demo-app
paths:
  runtime_root: /tmp/audio-chat/demo
asset:
  root: /tmp/audio-chat/custom-assets
memory:
  path: /tmp/audio-chat/custom-memory
dev_checks:
  report_path: /tmp/audio-chat/custom-preflight.json
""".lstrip(),
        encoding="utf-8",
    )

    config = load_yaml_config(config_path)

    assert config.observability.runs_root == "/tmp/audio-chat/demo"
    assert config.user.message_store["root"] == "/tmp/audio-chat/demo/users"
    assert config.asset.root == "/tmp/audio-chat/custom-assets"
    assert config.memory.path == "/tmp/audio-chat/custom-memory"
    assert config.dev_checks.report_path == "/tmp/audio-chat/custom-preflight.json"


def test_audio_chat_runs_root_env_updates_derived_paths(tmp_path: Path, monkeypatch) -> None:
    """测试目标：确认环境变量覆盖运行根目录时，派生路径一起跟随变化。

    测试方法：设置 AUDIO_CHAT_RUNS_ROOT 后读取只包含 app-name 的配置。
    预期结果：runs_root、资产、记忆和 preflight 报告都使用环境变量指定的根目录。
    """

    monkeypatch.setenv("AUDIO_CHAT_RUNS_ROOT", "/tmp/audio-chat/env-root")
    config_path = tmp_path / "server.yaml"
    config_path.write_text("app-name: demo-app\n", encoding="utf-8")

    config = load_yaml_config(config_path)

    assert config.paths.runtime_root == "/tmp/audio-chat/env-root"
    assert config.observability.runs_root == "/tmp/audio-chat/env-root"
    assert config.asset.root == "/tmp/audio-chat/env-root/assets"
    assert config.memory.path == "/tmp/audio-chat/env-root/memory"
    assert config.dev_checks.report_path == "/tmp/audio-chat/env-root/preflight.json"
