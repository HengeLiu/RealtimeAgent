from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_old_sdk_parity_voice_lane_is_registered() -> None:
    """测试目标：验证 G 线验收已接入统一 acceptance 脚本。

    测试方法：读取 `scripts/acceptance_check.py`。
    预期结果：包含 old-sdk-parity-voice lane 和 G 线关键测试文件。
    """

    script = (ROOT / "scripts" / "acceptance_check.py").read_text(encoding="utf-8")

    assert "old-sdk-parity-voice" in script
    assert "tests/test_voice_session_modes.py" in script
    assert "tests/test_progress_audio.py" in script
    assert "tests/test_continuous_dialog_state.py" in script
    assert "tests/test_playback_interrupt_policy.py" in script


def test_voice_old_sdk_parity_plan_terms_have_runtime_tests() -> None:
    """测试目标：验证 G 线计划中的核心能力都有对应运行时测试覆盖。

    测试方法：读取本阶段计划和 G 线测试源码，做轻量合同检查。
    预期结果：配置语义、前置播报、turn ignored、close_after_reply 和 interrupt 均有测试入口。
    """

    plan = (ROOT / "docs" / "old-sdk-parity-development-plan.md").read_text(encoding="utf-8")
    tests = "\n".join(
        [
            (ROOT / "tests" / "test_voice_session_modes.py").read_text(encoding="utf-8"),
            (ROOT / "tests" / "test_progress_audio.py").read_text(encoding="utf-8"),
            (ROOT / "tests" / "test_continuous_dialog_state.py").read_text(encoding="utf-8"),
            (ROOT / "tests" / "test_playback_interrupt_policy.py").read_text(encoding="utf-8"),
            (ROOT / "tests" / "test_audio_session_lifecycle.py").read_text(encoding="utf-8"),
        ]
    )

    assert "并行线路 G" in plan
    for keyword in [
        "voice.server_mode",
        "agent.mode=realtime",
        "tool.progress_message.emitted",
        "control.audio_session.turn.ignored",
        "close_after_reply",
        "wake_word_interrupt",
    ]:
        assert keyword in tests
