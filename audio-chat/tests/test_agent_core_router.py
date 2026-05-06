import pytest

from audio_chat.app import AudioChatApp, AudioChatConfig


def test_agent_mode_text_builds_text_core(tmp_path) -> None:
    """测试目标：验证 `agent.mode=text` 是当前可运行 Agent Core。

    测试方法：用 text 模式创建 AudioChatApp。
    预期结果：app 正常初始化，并带有 `append_audio_event` 方法。
    """
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), agent_mode="text"))

    assert hasattr(app.text_agent_core, "append_audio_event")


def test_agent_mode_realtime_audio_fails_fast(tmp_path) -> None:
    """测试目标：验证 realtime audio 模式尚未实现时给出明确错误。

    测试方法：用 `agent_mode=realtime_audio` 创建 AudioChatApp。
    预期结果：抛出 `NotImplementedError`，避免静默落入 TextAgentCore。
    """
    with pytest.raises(NotImplementedError, match="realtime_audio"):
        AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), agent_mode="realtime_audio"))
