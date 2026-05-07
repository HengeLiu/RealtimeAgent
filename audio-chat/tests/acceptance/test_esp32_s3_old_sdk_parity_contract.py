from __future__ import annotations

from scripts import acceptance_check


def test_old_sdk_parity_esp32_lane_is_registered() -> None:
    """测试目标：确认 ESP32-S3 老 SDK 对齐线路进入统一验收脚本。

    测试方法：读取 `scripts.acceptance_check.CHECKS` 中的 lane 注册表。
    预期结果：`old-sdk-parity-esp32` 存在，且覆盖 ESP32 契约、配置和 package manifest。
    """

    assert "old-sdk-parity-esp32" in acceptance_check.CHECKS
    command_text = "\n".join(" ".join(command.command) for command in acceptance_check.CHECKS["old-sdk-parity-esp32"])
    assert "tests/test_esp32_s3_endpoint_contract.py" in command_text
    assert "tests/test_esp32_package_manifest.py" in command_text


def test_esp32_contract_keeps_event_stream_boundary() -> None:
    """测试目标：冻结 ESP32-S3 参考端的通讯边界。

    测试方法：导入根契约测试中的状态机并检查注册 payload。
    预期结果：设备通过 capability/subscription 声明音频和 RGB 能力，不出现隐藏 RPC。
    """

    from audio_chat.endpoints.esp32_aec import Esp32AecEndpointState

    payload = Esp32AecEndpointState(device_id="dev-esp32", user_id="user-esp32").registration_payload()

    assert "sensor.mic" in payload["capabilities"]["streams.produce"]
    assert "sensor.rgb" in payload["capabilities"]["streams.produce"]
    assert {"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}} in payload["subscriptions"]
    assert "capture_photo" not in str(payload)
    assert "target_device" not in str(payload)
