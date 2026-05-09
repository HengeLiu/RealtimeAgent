from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IOS_ROOT = ROOT / "device-examples" / "native-ios-phone"


def test_ios_phone_reference_contains_task_registry_contract() -> None:
    """测试目标：验证 iOS phone 参考端具备 phone task 契约样板。

    测试方法：静态检查 Swift 运行时和配置，确认注册了 command 订阅、任务 registry
    和 find_object / traffic_light handler。
    预期结果：iOS 端可通过 contract 验收 phone task 事件链，不需要 Python 代码复用。
    """

    runtime = (IOS_ROOT / "AudioChatPhone/Core/AudioChatEndpointRuntime.swift").read_text(encoding="utf-8")
    config = (IOS_ROOT / "AppConfig.example.json").read_text(encoding="utf-8")

    for token in [
        "PhoneTaskRegistry",
        "FindObjectPhoneTaskHandler",
        "TrafficLightPhoneTaskHandler",
        "command.requested",
        "command.accepted",
        "command.progress",
        "command.completed",
        "phone.task.find_object_phone_task",
        "phone.task.traffic_light_phone_task",
        "DirectCameraSinkServer",
        "direct.camera_sink",
        "audio_chat.direct_frame.v1",
    ]:
        assert token in runtime + config

    assert "target_device" not in runtime
    assert "target_device_id" not in runtime
