"""ESP32 眼镜运行时源码边界测试。"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
GLASS_MAIN = ROOT / "openaiglass-sdk" / "glass-esp32" / "main" / "glass_main.c"


def test_glass_runtime_negotiates_realtime_voice_capabilities() -> None:
    """测试目标：真实眼镜固件必须能响应服务端默认实时语音打开请求并上报 AEC 能力。

    测试方法：
    1. 静态读取 ESP32 主运行时源码。
    2. 检查是否处理 `voice.realtime.session.open`。
    3. 检查回复是否按 AEC 初始化结果声明实际接受模式和端侧能力。

    预期结果：
    1. 真实眼镜不会因为服务端默认 `full_duplex_realtime` 而一直不开 WakeNet 门控。
    2. AEC 初始化成功时端侧可上报全双工插话能力，失败时仍可回退半双工。
    """

    source = GLASS_MAIN.read_text(encoding="utf-8")

    assert "voice.realtime.session.open" in source
    assert "voice.realtime.session.opened" in source
    assert 's_aec_runtime_enabled ? "full_duplex_realtime" : "half_duplex"' in source
    assert 'cJSON_AddBoolToObject(capabilities, "aec", s_aec_runtime_enabled)' in source
    assert 'cJSON_AddBoolToObject(capabilities, "barge_in", s_aec_runtime_enabled)' in source
    assert 'cJSON_AddBoolToObject(capabilities, "output_cancel", s_aec_runtime_enabled)' in source
    assert "ensure_audio_transport_started();" in source
    assert "WakeNet listening enabled for realtime session_id=%s accepted_mode=%s" in source
    assert "xTaskCreateWithCaps(" in source
    assert "PLAYBACK_STREAM_TASK_STACK_SIZE" in source
    assert "CONFIG_SPIRAM_ALLOW_STACK_EXTERNAL_MEMORY" in source
    assert "CONFIG_GLASS_BARGE_IN_GRACE_MS" in source
    assert "CONFIG_GLASS_BARGE_IN_MIN_SPEECH_FRAMES" in source
    assert "忽略播放起始保护窗内的 VAD" in source
    assert "播放中 VAD 仍在确认用户插话" in source
    assert "播放中 VAD 触发候选语音段，等待 Omni semantic_vad 确认" in source
    assert '"started_during_playback"' in source
    assert '"playback_stream_id"' in source
    assert 'send_user_voice_interrupt_message(interrupted_stream_id, "voice_barge_in")' not in source
    assert "s_playback_speaker_started_ms = now_ms()" in source
    assert "external_stack_allowed=%d" in source


def test_glass_runtime_plays_short_prompt_tone_on_wakenet() -> None:
    """测试目标：验证真实眼镜在首次 WakeNet 唤醒后会播放短促提示音。

    测试方法：
    1. 静态读取 ESP32 主运行时源码。
    2. 检查提示音只挂在 `start_by_wake_word` 分支，而不是连续 VAD 分支。
    3. 检查提示音会写入 AEC 参考缓冲，降低提示音被当作用户语音的风险。

    预期结果：
    1. 首次唤醒成功后端侧有本地轻提示。
    2. 连续对话窗口内的后续 VAD 追问不会重复播放提示音。
    """

    source = GLASS_MAIN.read_text(encoding="utf-8")
    wake_branch = source[source.index("if (start_by_wake_word)") : source.index("} else {", source.index("if (start_by_wake_word)"))]
    vad_branch = source[source.index("} else {", source.index("if (start_by_wake_word)")) : source.index("send_audio_segment_started_message", source.index("if (start_by_wake_word)"))]

    assert "CONFIG_GLASS_WAKE_PROMPT_TONE_ENABLE" in source
    assert "play_wake_prompt_tone();" in wake_branch
    assert "play_wake_prompt_tone();" not in vad_branch
    assert "push_aec_reference_samples(mono_buffer" in source
    assert "唤醒成功提示音已播放" in source
    assert "mono_buffer = heap_caps_malloc" in source
    assert "stereo_buffer = heap_caps_malloc" in source
    assert "int16_t mono_buffer[AUDIO_FRAME_SAMPLES]" not in source
    assert "int32_t stereo_buffer[AUDIO_FRAME_SAMPLES * 2]" not in source
    assert "WakeNet 初始化完成后已补发实时语音能力" in source
    assert "服务端回复已开始，提前关闭当前本地语音段" in source
    assert "s_local_segment_active = true" in source
    assert "s_local_segment_active = false" in source
    assert "s_continuous_dialog_active && s_local_segment_active" in source
    assert 'finish_reason", finish_reason' in source
    assert '"server_response_started"' in source
    assert 'build_runtime_token("stream", s_current_stream_id' in source
