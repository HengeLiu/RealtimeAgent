from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml

from audio_chat.app import AudioChatConfig
from audio_chat.config import load_yaml_config


AUDIO_ROOT = Path(__file__).resolve().parents[2]


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
    预期结果：生成的 glass.playback.yaml 使用应用目录下的 `runs` 作为 runs_root。
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
    assert playback["runs_root"] == str(app_root / "runs")


def test_config_loads_observability_log_timezone(tmp_path) -> None:
    """测试目标：确认 YAML 中的日志时区会进入运行配置。

    测试方法：写入最小 server.yaml，并在 observability 中设置 `Asia/Shanghai`。
    预期结果：加载后的 YAML 配置和运行态配置都保留该时区。
    """

    config_path = tmp_path / "server.yaml"
    config_path.write_text(
        """
observability:
  log_timezone: Asia/Shanghai
""".lstrip(),
        encoding="utf-8",
    )

    loaded = load_yaml_config(config_path)
    runtime_config = AudioChatConfig.from_loaded_config(loaded)

    assert loaded.observability.log_timezone == "Asia/Shanghai"
    assert runtime_config.log_timezone == "Asia/Shanghai"


def test_config_loads_message_compaction_policy(tmp_path) -> None:
    """测试目标：确认会话摘要压缩策略可由 YAML 配置控制。

    测试方法：写入 user.message_compact_threshold 和 keep_latest 后加载运行配置。
    预期结果：运行时会使用配置值，而不是固定的默认阈值。
    """

    config_path = tmp_path / "server.yaml"
    config_path.write_text(
        """
user:
  message_compact_threshold: 10
  message_compact_keep_latest: 4
""".lstrip(),
        encoding="utf-8",
    )

    loaded = load_yaml_config(config_path)
    runtime_config = AudioChatConfig.from_loaded_config(loaded)

    assert loaded.user.message_compact_threshold == 10
    assert loaded.user.message_compact_keep_latest == 4
    assert runtime_config.message_compact_threshold == 10
    assert runtime_config.message_compact_keep_latest == 4


def test_agent_prompt_and_provider_config_names_are_canonical(tmp_path) -> None:
    """测试目标：确认 Agent 配置统一使用 `provider/model/prompt` 命名。

    测试方法：写入同时包含 text 与 realtime 的最小 YAML，并读取配置对象。
    预期结果：文本链路和 Realtime 链路都通过同名字段取得 provider、model 和 prompt。
    """

    config_path = tmp_path / "server.yaml"
    config_path.write_text(
        """
agent:
  mode: realtime_audio
  text:
    provider: mock
    model: mock-text
    prompt: 文本提示词
  realtime:
    provider: mock
    model: mock-realtime
    prompt: 实时提示词
    max_concurrent_sessions: 7
""".lstrip(),
        encoding="utf-8",
    )

    loaded = load_yaml_config(config_path)
    runtime_config = AudioChatConfig.from_loaded_config(loaded)

    assert loaded.agent.text.provider == "mock"
    assert loaded.agent.text.prompt == "文本提示词"
    assert loaded.agent.realtime.provider == "mock"
    assert loaded.agent.realtime.prompt == "实时提示词"
    assert loaded.agent.realtime.max_concurrent_sessions == 7
    assert runtime_config.text_provider == "mock"
    assert runtime_config.text_prompt == "文本提示词"
    assert runtime_config.realtime_provider == "mock"
    assert runtime_config.realtime_prompt == "实时提示词"
    assert runtime_config.realtime_max_concurrent_sessions == 7


def test_agent_text_multimodal_config_is_loaded(tmp_path) -> None:
    """测试目标：确认 Text 多模态配置会从 YAML 同步到运行时配置。

    测试方法：写入包含 agent.text.multimodal 的最小 YAML，加载后检查 loaded 和
    AudioChatConfig 字段。
    预期结果：图片、抓拍次数和视频配置都能被运行时读取。
    """

    config_path = tmp_path / "server.yaml"
    config_path.write_text(
        """
agent:
  text:
    provider: dashscope-compatible
    model: qwen3.6-flash
    multimodal:
      enabled: true
      attach_tool_result_assets: true
      max_images_per_turn: 2
      max_image_base64_bytes: 12345
      max_capture_photo_calls_per_turn: 1
      video:
        enabled: true
        prefer_native_video: true
        max_inline_bytes: 54321
""".lstrip(),
        encoding="utf-8",
    )

    loaded = load_yaml_config(config_path)
    runtime_config = AudioChatConfig.from_loaded_config(loaded)

    assert loaded.agent.text.multimodal.enabled is True
    assert loaded.agent.text.multimodal.max_images_per_turn == 2
    assert loaded.agent.text.multimodal.video.enabled is True
    assert runtime_config.text_multimodal_enabled is True
    assert runtime_config.text_multimodal_attach_tool_result_assets is True
    assert runtime_config.text_multimodal_max_image_base64_bytes == 12345
    assert runtime_config.text_multimodal_video_max_inline_bytes == 54321
