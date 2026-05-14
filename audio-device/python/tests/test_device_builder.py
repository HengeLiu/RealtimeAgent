from audio_chat.device_capabilities import validate_device_capabilities_file
from audio_chat_device import DeviceBuilder


def test_device_builder_outputs_structured_supports_payload() -> None:
    """测试目标：确认 Python 端侧 SDK 能生成 server 可接受的设备注册 payload。

    测试方法：使用 `DeviceBuilder` 声明 RGB 和震动能力，再调用当前运行时能力校验。
    预期结果：payload 不需要开发者手写 routes，并且 supports 能通过校验。
    """

    device = (
        DeviceBuilder.define("dev-python-001")
        .user("user-001")
        .name("Python Device")
        .role("glass")
        .sensor_rgb(modes=["single", "continuous"], format="jpeg", frequency_hz=1)
        .actuator_vibrator(["vibrate"])
    )

    payload = device.registration_payload()

    assert payload["device_id"] == "dev-python-001"
    assert payload["properties"]["device_role"] == "glass"
    assert "routes" not in payload
    assert validate_device_capabilities_file({"user_id": "user-001", **payload})
