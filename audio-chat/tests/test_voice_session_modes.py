from __future__ import annotations

from pathlib import Path

from audio_chat.agent_core.realtime import RealtimeAudioAgentCore
from audio_chat.agent_core.text import TextAgentCore
from audio_chat.app import AudioChatApp, AudioChatConfig


def test_agent_mode_realtime_alias_builds_realtime_core(tmp_path) -> None:
    """测试目标：验证 `agent.mode=realtime` 兼容老计划中的配置说法。

    测试方法：直接用 `AudioChatConfig(agent_mode="realtime")` 创建 App。
    预期结果：实际构建 `RealtimeAudioAgentCore`，且不在构造阶段连接真实 provider。
    """

    app = AudioChatApp(
        AudioChatConfig(runs_root=str(tmp_path / "runs"), agent_mode="realtime", realtime_provider="mock")
    )

    assert isinstance(app.agent_core, RealtimeAudioAgentCore)


def test_voice_server_mode_text_and_omni_map_to_agent_modes(tmp_path) -> None:
    """测试目标：验证旧 SDK `voice.server_mode` 可以迁移到新版 Agent Core。

    测试方法：分别写入 text_server 和 omni_server YAML，再通过 `AudioChatConfig.from_yaml()` 加载。
    预期结果：text_server 构建 TextAgentCore，omni_server 构建 RealtimeAudioAgentCore。
    """

    text_config = tmp_path / "text.yaml"
    text_config.write_text("voice:\n  server_mode: text_server\nobservability:\n  runs_root: runs-text\n", encoding="utf-8")
    text_app = AudioChatApp(AudioChatConfig.from_yaml(text_config))

    omni_config = tmp_path / "omni.yaml"
    omni_config.write_text(
        "voice:\n  server_mode: omni_server\nagent:\n  realtime:\n    provider: mock\nobservability:\n  runs_root: runs-omni\n",
        encoding="utf-8",
    )
    omni_app = AudioChatApp(AudioChatConfig.from_yaml(omni_config))

    assert isinstance(text_app.agent_core, TextAgentCore)
    assert isinstance(omni_app.agent_core, RealtimeAudioAgentCore)
    assert omni_app.config.voice_server_mode == "omni_server"


def test_voice_session_lifecycle_config_is_exposed(tmp_path) -> None:
    """测试目标：验证 `voice.session_lifecycle` 进入运行配置。

    测试方法：加载包含 persistent/per_turn 语义的 YAML。
    预期结果：配置对象保留该语义，后续 App 生命周期判断可读取。
    """

    config_path = tmp_path / "voice.yaml"
    config_path.write_text(
        "\n".join(
            [
                "voice:",
                "  conversation_mode: continuous",
                "  session_lifecycle: persistent",
                "observability:",
                f"  runs_root: {Path(tmp_path / 'runs').as_posix()}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    app = AudioChatApp(AudioChatConfig.from_yaml(config_path))

    assert app.config.voice_conversation_mode == "continuous"
    assert app.config.voice_session_lifecycle == "persistent"
