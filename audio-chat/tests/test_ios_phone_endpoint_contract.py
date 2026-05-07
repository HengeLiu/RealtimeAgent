from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IOS_ROOT = ROOT / "endpoints" / "ios-phone"


def _read(relative: str) -> str:
    return (IOS_ROOT / relative).read_text(encoding="utf-8")


def test_ios_phone_project_and_config_are_present() -> None:
    """测试目标：验证 iOS phone 参考端已经从协议锚点推进到可打开工程。

    测试方法：检查 Xcode project、共享 scheme、AppConfig 示例和 App 内配置资源。
    预期结果：开发者可以直接打开 `AudioChatPhone.xcodeproj`，并替换 `AppConfig.json`
    进行 Simulator 或真机协议联调。
    """

    assert (IOS_ROOT / "AudioChatPhone.xcodeproj/project.pbxproj").exists()
    assert (IOS_ROOT / "AudioChatPhone.xcodeproj/xcshareddata/xcschemes/AudioChatPhone.xcscheme").exists()
    assert (IOS_ROOT / "AudioChatPhone/AudioChatPhoneApp.swift").exists()
    assert (IOS_ROOT / "AudioChatPhone/Resources/AppConfig.json").exists()
    assert (IOS_ROOT / "AppConfig.example.json").exists()


def test_ios_phone_config_schema_matches_endpoint_protocol() -> None:
    """测试目标：验证 iOS 配置字段与其他参考端侧保持同一语义。

    测试方法：读取 `AppConfig.example.json`，检查 server、user、device、auth、
    capabilities 和 subscriptions。
    预期结果：iOS 不引入专用配置字段，能力路由仍由 capability/subscription 决定。
    """

    config = json.loads((IOS_ROOT / "AppConfig.example.json").read_text(encoding="utf-8"))

    assert config["server_url"].startswith("http://")
    assert config["user_id"] == "user-endpoint-001"
    assert config["device_id"] == "dev-ios-phone-001"
    assert config["auth"]["mode"] == "disabled"
    assert config["protocol_version"] == "audio-chat.v1"
    assert "sensor.rgb" in config["capabilities"]["streams.produce"]
    assert "sensor.mic" in config["capabilities"]["streams.produce"]
    assert "actuator.speaker" in config["capabilities"]["streams.consume"]
    assert config["capabilities"]["phone.task.find_object_phone_task"] is True
    assert config["capabilities"]["phone.task.traffic_light_phone_task"] is True
    assert {"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}} in config["subscriptions"]
    assert {"event": "stream.output.*", "filter": {"stream_type": "actuator.speaker"}} in config["subscriptions"]
    assert {"event": "control.device.command.*"} in config["subscriptions"]


def test_ios_phone_registration_event_matches_contract_golden() -> None:
    """测试目标：验证 iOS 注册事件遵守公共 control.device.register.requested 契约。

    测试方法：读取 iOS 专用 golden 和 Swift 源码，检查注册 payload 必备字段。
    预期结果：注册事件携带 user_id、device_id、auth、capabilities 和 subscriptions，
    不包含 target_device 或固定 phone/glass 路由字段。
    """

    golden = json.loads((ROOT / "testdata/contracts/endpoints/ios_phone_register_requested.json").read_text(encoding="utf-8"))
    payload = golden["payload"]

    assert golden["event_name"] == "control.device.register.requested"
    assert golden["producer_id"] == payload["device_id"]
    assert payload["client_type"] == "ios-phone"
    assert payload["auth"]["mode"] == "disabled"
    assert "sensor.rgb" in payload["capabilities"]["streams.produce"]
    assert "sensor.mic" in payload["capabilities"]["streams.produce"]
    assert "actuator.speaker" in payload["capabilities"]["streams.consume"]
    assert "target_device" not in json.dumps(golden)
    assert "target_device_id" not in json.dumps(golden)

    source = _read("AudioChatPhone/Core/AudioChatEndpointRuntime.swift")
    for token in [
        "control.device.register.requested",
        "\"auth\": config.auth.payload",
        "\"capabilities\": config.capabilities.mapValues",
        "\"subscriptions\": config.subscriptions.map",
    ]:
        assert token in source


def test_ios_phone_handles_control_and_stream_events_without_hidden_rpc() -> None:
    """测试目标：验证 iOS 参考端只通过 event / stream 协议处理端侧能力。

    测试方法：静态检查 Swift 代码中的事件名、stream 类型和禁止旧 RPC 的关键词。
    预期结果：iOS 能处理输出 stream、响应 sensor.rgb 采集请求、上传测试 PCM，
    且没有 `capture_photo` 或固定设备路由字段。
    """

    runtime = _read("AudioChatPhone/Core/AudioChatEndpointRuntime.swift")
    codec = _read("AudioChatPhone/Core/StreamChunkCodec.swift")
    combined = runtime + codec

    required_tokens = [
        "/ws/control",
        "/ws/stream",
        "stream.control.configure.requested",
        "stream.output.started",
        "stream.output.finished",
        "stream.output.closed",
        "control.device.command.requested",
        "control.device.command.started",
        "control.device.command.progress",
        "control.device.command.completed",
        "stream.input.opened",
        "stream.input.closed",
        "sensor.rgb",
        "sensor.mic",
        "actuator.speaker",
        "PhoneTaskRegistry",
        "FindObjectPhoneTaskHandler",
        "TrafficLightPhoneTaskHandler",
        "payload_size",
        "UInt32(headerData.count).bigEndian",
    ]
    for token in required_tokens:
        assert token in combined

    forbidden_tokens = ["capture_photo", "target_device", "target_device_id", "/api/tasks/report-event"]
    for token in forbidden_tokens:
        assert token not in combined


def test_ios_phone_readme_documents_simulator_real_device_and_signed_token() -> None:
    """测试目标：验证 README 覆盖可运行客户端的启动和鉴权说明。

    测试方法：检查 README 是否说明 config sync、Simulator build、真机配置和
    signed_token 生成提示。
    预期结果：无 Xcode 环境时 contract test 仍能约束文档，避免只提交代码骨架。
    """

    readme = _read("README.md")
    for token in [
        "AudioChatPhone.xcodeproj",
        "xcodebuild -scheme AudioChatPhone",
        "audio-chat.config.sync",
        "AppConfig.json",
        "signed_token",
        "sensor.rgb",
        "actuator.speaker",
    ]:
        assert token in readme
