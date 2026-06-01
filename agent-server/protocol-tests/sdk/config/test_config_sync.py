from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml

from realtime_agent.app import RealtimeAgentConfig
from realtime_agent.config import load_yaml_config


AUDIO_ROOT = Path(__file__).resolve().parents[4]


def test_config_sync_generates_developer_files(tmp_path) -> None:
    """测试目标：确认 `realtime-agent.config.sync` 能生成开发者本地配置。

    测试方法：把 app-root 指向临时目录，指定 output-dir 后执行同步命令。
    预期结果：生成 server、phone mock、glass playback 三类配置和 sync-result.json。
    """

    app_root = tmp_path / "device_demo"
    output_dir = tmp_path / "generated"
    completed = subprocess.run(
        [
            "uv",
            "run",
            "realtime-agent.config.sync",
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
            "realtime-agent.config.sync",
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
    runtime_config = RealtimeAgentConfig.from_loaded_config(loaded)

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
    runtime_config = RealtimeAgentConfig.from_loaded_config(loaded)

    assert loaded.user.message_compact_threshold == 10
    assert loaded.user.message_compact_keep_latest == 4
    assert runtime_config.message_compact_threshold == 10
    assert runtime_config.message_compact_keep_latest == 4


def test_agent_prompt_and_provider_config_names_are_canonical(tmp_path) -> None:
    """测试目标：确认 Agent 配置统一使用 `provider/model/prompt` 命名。

    测试方法：写入同时包含 vision 与 omni 的最小 YAML，并读取配置对象。
    预期结果：Vision 链路和 Omni 链路都通过同名字段取得 provider、model 和 prompt。
    """

    config_path = tmp_path / "server.yaml"
    config_path.write_text(
        """
agent:
  mode: omni
  vision:
    provider: mock
    model: mock-vision
    prompt: 视觉提示词
  omni:
    provider: mock
    model: mock-omni
    prompt: Omni提示词
    max_concurrent_sessions: 7
""".lstrip(),
        encoding="utf-8",
    )

    loaded = load_yaml_config(config_path)
    runtime_config = RealtimeAgentConfig.from_loaded_config(loaded)

    assert loaded.agent.vision.provider == "mock"
    assert loaded.agent.vision.prompt == "视觉提示词"
    assert loaded.agent.omni.provider == "mock"
    assert loaded.agent.omni.prompt == "Omni提示词"
    assert loaded.agent.omni.max_concurrent_sessions == 7
    assert runtime_config.vision_provider == "mock"
    assert runtime_config.vision_prompt == "视觉提示词"
    assert runtime_config.omni_provider == "mock"
    assert runtime_config.omni_prompt == "Omni提示词"
    assert runtime_config.omni_max_concurrent_sessions == 7


def test_agent_text_multimodal_config_is_loaded(tmp_path) -> None:
    """测试目标：确认 Vision 多模态配置会从 YAML 同步到运行时配置。

    测试方法：写入包含 agent.vision.multimodal 的最小 YAML，加载后检查 loaded 和
    RealtimeAgentConfig 字段。
    预期结果：图片、抓拍次数和视频配置都能被运行时读取。
    """

    config_path = tmp_path / "server.yaml"
    config_path.write_text(
        """
agent:
  vision:
    provider: dashscope-compatible
    model: qwen3.6-flash
    multimodal:
      enabled: true
      attach_visual_assets: true
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
    runtime_config = RealtimeAgentConfig.from_loaded_config(loaded)

    assert loaded.agent.vision.multimodal.enabled is True
    assert loaded.agent.vision.multimodal.max_images_per_turn == 2
    assert loaded.agent.vision.multimodal.video.enabled is True
    assert runtime_config.vision_multimodal_enabled is True
    assert runtime_config.vision_multimodal_attach_visual_assets is True
    assert runtime_config.vision_multimodal_max_image_base64_bytes == 12345
    assert runtime_config.vision_multimodal_video_max_inline_bytes == 54321


def test_agent_visual_realtime_video_config_is_loaded(tmp_path) -> None:
    """测试目标：确认全局 realtime-video 配置会从 YAML 同步到运行时配置。

    测试方法：写入 `agent.visual.realtime_video` 配置并加载。
    预期结果：运行时配置读取 frame 间隔、TTL、上限和 direction，且不依赖旧
    `agent.omni.visual_frame_*` 字段。
    """

    config_path = tmp_path / "server.yaml"
    config_path.write_text(
        """
agent:
  visual:
    realtime_video:
      enabled: true
      frame_interval_seconds: 0.5
      frame_timeout_seconds: 1.2
      frame_ttl_seconds: 6
      max_frames_per_turn: 3
      direction: front
""".lstrip(),
        encoding="utf-8",
    )

    loaded = load_yaml_config(config_path)
    runtime_config = RealtimeAgentConfig.from_loaded_config(loaded)

    assert runtime_config.visual_realtime_video_enabled is True
    assert runtime_config.visual_realtime_video_frame_interval_seconds == 0.5
    assert runtime_config.visual_realtime_video_frame_timeout_seconds == 1.2
    assert runtime_config.visual_realtime_video_frame_ttl_seconds == 6
    assert runtime_config.visual_realtime_video_max_frames_per_turn == 3
    assert runtime_config.visual_realtime_video_direction == "front"
