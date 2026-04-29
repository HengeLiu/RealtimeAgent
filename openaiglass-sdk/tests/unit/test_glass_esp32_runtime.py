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
    assert "创建 playback_stream_task 失败: stack=%d" in source
