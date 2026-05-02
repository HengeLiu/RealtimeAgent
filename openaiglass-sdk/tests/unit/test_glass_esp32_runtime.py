"""ESP32 眼镜运行时源码边界测试。"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
GLASS_MAIN = ROOT / "openaiglass-sdk" / "glass-esp32" / "main" / "glass_main.c"


def test_glass_runtime_degrades_realtime_voice_open_to_half_duplex() -> None:
    """测试目标：真实眼镜固件必须能响应服务端默认实时语音打开请求。

    测试方法：
    1. 静态读取 ESP32 主运行时源码。
    2. 检查是否处理 `voice.realtime.session.open`。
    3. 检查回复是否声明半双工降级和不支持端侧 AEC。

    预期结果：
    1. 真实眼镜不会因为服务端默认 `full_duplex_realtime` 而一直不开 WakeNet 门控。
    2. 服务端可通过 `capabilities.aec=false` 把实时会话降级为半双工。
    """

    source = GLASS_MAIN.read_text(encoding="utf-8")

    assert "voice.realtime.session.open" in source
    assert "voice.realtime.session.opened" in source
    assert 'cJSON_AddStringToObject(payload, "accepted_mode", "half_duplex")' in source
    assert 'cJSON_AddBoolToObject(capabilities, "aec", false)' in source
    assert 'cJSON_AddBoolToObject(capabilities, "barge_in", false)' in source
    assert 'cJSON_AddBoolToObject(capabilities, "output_cancel", false)' in source
    assert "s_realtime_semantic_dialog_enabled = false;" in source
    assert "semantic_continuous_requested=%d semantic_continuous_enabled=%d" in source
    assert "ensure_audio_transport_started();" in source
    assert "WakeNet listening enabled for realtime-degraded session_id=%s" in source
    assert "!s_playback_active" in source
    assert "CONFIG_GLASS_ENABLE_AEC" not in source
    assert "started_during_playback" not in source
    assert '"playback_stream_id"' not in source
    assert "播放中 VAD 触发候选语音段" not in source


def test_glass_runtime_marks_audio_segment_trigger_source() -> None:
    """测试目标：验证眼镜上报语音段时会区分 WakeNet 和连续 VAD 触发来源。

    测试方法：
    1. 静态读取 ESP32 主运行时源码。
    2. 检查 `sensor.audio.segment.started` 会写入 `trigger` 字段。
    3. 检查 WakeNet 分支保留 `wake_word` 元信息，连续 VAD 分支不会伪装成唤醒词触发。

    预期结果：
    1. 服务端能识别连续 VAD 产生的后续段。
    2. 服务端可以对无转写的连续 VAD 空段做抑制，避免进入自动看图自循环。
    """

    source = GLASS_MAIN.read_text(encoding="utf-8")

    assert 'cJSON_AddStringToObject(payload, "trigger", started_by_wake_word ? "wake_word" : "continuous_vad")' in source
    assert "cJSON *wake_word = started_by_wake_word ? cJSON_CreateObject() : NULL;" in source
    assert "send_audio_segment_started_message(segment.segment_id, start_by_wake_word);" in source


def test_glass_runtime_plays_short_prompt_tone_on_wakenet() -> None:
    """测试目标：验证真实眼镜在首次 WakeNet 唤醒后会播放短促提示音。

    测试方法：
    1. 静态读取 ESP32 主运行时源码。
    2. 检查提示音只挂在 `start_by_wake_word` 分支，而不是连续 VAD 分支。
    3. 检查提示音不依赖 AEC 参考缓冲，避免回退方案 A 时重新引入播放中插话链路。

    预期结果：
    1. 首次唤醒成功后端侧有本地轻提示。
    2. 连续对话窗口内的后续 VAD 追问不会重复播放提示音。
    3. 提示音实现保持轻量，不重新引入 AEC 相关代码。
    """

    source = GLASS_MAIN.read_text(encoding="utf-8")
    wake_branch = source[source.index("if (start_by_wake_word)") : source.index("} else {", source.index("if (start_by_wake_word)"))]
    vad_branch = source[source.index("} else {", source.index("if (start_by_wake_word)")) : source.index("send_audio_segment_started_message", source.index("if (start_by_wake_word)"))]

    assert "CONFIG_GLASS_WAKE_PROMPT_TONE_ENABLE" in source
    assert "play_wake_prompt_tone();" in wake_branch
    assert "play_wake_prompt_tone();" not in vad_branch
    assert "push_aec_reference_samples" not in source
    assert "唤醒成功提示音已播放" in source
    assert "mono_buffer = heap_caps_malloc" in source
    assert "stereo_buffer = heap_caps_malloc" in source
    assert "int16_t mono_buffer[AUDIO_FRAME_SAMPLES]" not in source
    assert "int32_t stereo_buffer[AUDIO_FRAME_SAMPLES * 2]" not in source
