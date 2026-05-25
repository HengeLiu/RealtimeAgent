from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
IOS_ROOT = ROOT / "examples" / "for-blind-app" / "devices" / "native-ios-phone"


def test_ios_phone_reference_uses_device_sdk_event_contract() -> None:
    """测试目标：验证 iOS phone 参考端使用新版 Device SDK 事件契约。

    测试方法：静态检查 Swift 运行时和配置，确认通过 `DeviceClient`、custom event
    和显式硬件 enable 接入，不再保留旧 phone task registry 样板。
    预期结果：iOS 参考端以 Device SDK 语法糖接入注册、事件和硬件能力。
    """

    runtime = (IOS_ROOT / "RealtimeAgentPhone/Core/RealtimeAgentEndpointRuntime.swift").read_text(encoding="utf-8")
    config = (IOS_ROOT / "AppConfig.example.json").read_text(encoding="utf-8")

    for token in [
        "DeviceClient(",
        "RealtimeAgentDeviceClient",
        "onCustomCommand",
        "custom.haptic.vibrate.done",
        "AudioInput",
        "Camera",
        "Speaker",
        "audio_input",
        "camera",
        "speaker",
        "direct.camera_sink",
        "realtime_agent.direct_frame.v1",
    ]:
        assert token in runtime + config

    assert "PhoneTaskRegistry" not in runtime
    assert "command.accepted" not in runtime
    assert "command.progress" not in runtime
    assert "command.completed" not in runtime
    assert "target_device" not in runtime
    assert "target_device_id" not in runtime
