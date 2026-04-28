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
    assert "ensure_audio_transport_started();" in source
    assert "WakeNet listening enabled for realtime-degraded session_id=%s" in source
