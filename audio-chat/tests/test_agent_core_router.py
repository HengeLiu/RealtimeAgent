import pytest

from audio_chat.agent_core.realtime import RealtimeAudioAgentCore
from audio_chat.agent_core.text import TextAgentCore
from audio_chat.app import AudioChatApp, AudioChatConfig


def test_agent_mode_text_builds_text_core(tmp_path) -> None:
    """测试目标：验证 `agent.mode=text` 是当前可运行 Agent Core。

    测试方法：用 text 模式创建 AudioChatApp。
    预期结果：app 正常初始化，并带有 `append_audio_event` 方法。
    """
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), agent_mode="text"))

    assert isinstance(app.agent_core, TextAgentCore)
    assert hasattr(app.agent_core, "append_audio_event")


def test_agent_mode_realtime_audio_builds_realtime_core(tmp_path) -> None:
    """测试目标：验证 `agent.mode=realtime_audio` 能创建 RealtimeAudioAgentCore。

    测试方法：用 `agent_mode=realtime_audio` 创建 AudioChatApp。
    预期结果：app 正常初始化，不在构造阶段连接真实 provider。
    """
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), agent_mode="realtime_audio"))

    assert isinstance(app.agent_core, RealtimeAudioAgentCore)


def test_agent_mode_auto_defaults_to_text_for_now(tmp_path) -> None:
    """测试目标：验证 `agent.mode=auto` 当前保守落到文本链路。

    测试方法：用 auto 模式创建 AudioChatApp。
    预期结果：返回 TextAgentCore；文档中声明后续再接端侧能力判断。
    """
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), agent_mode="auto"))

    assert isinstance(app.agent_core, TextAgentCore)


def test_agent_mode_custom_fails_fast(tmp_path) -> None:
    """测试目标：验证 custom 模式没有 app-module 工厂时明确失败。

    测试方法：用 custom 模式创建 AudioChatApp。
    预期结果：抛出 NotImplementedError。
    """
    with pytest.raises(NotImplementedError, match="custom"):
        AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), agent_mode="custom"))
