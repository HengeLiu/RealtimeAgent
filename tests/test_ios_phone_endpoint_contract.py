from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IOS_ROOT = ROOT / "device-examples" / "native-ios-phone"


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
    properties、supports 和 subscriptions。
    预期结果：iOS 不引入专用配置字段，优先用 supports 声明设备语义能力。
    """

    config = json.loads((IOS_ROOT / "AppConfig.example.json").read_text(encoding="utf-8"))

    assert config["server_url"].startswith("http://")
    assert config["user_id"] == "user-endpoint-001"
    assert config["device_id"] == "dev-ios-phone-001"
    assert config["auth"]["mode"] == "disabled"
    assert config["protocol_version"] == "audio-chat.v1"
    assert config["direct_camera_sink_port"] == 9001
    assert config["properties"]["phone.task.find_object_phone_task"] is True
    assert config["properties"]["phone.task.traffic_light_phone_task"] is True
    assert config["properties"]["direct.camera_sink"] is True
    assert config["properties"]["direct.camera_sink.path"] == "/ws/camera"
    assert config["properties"]["direct.camera_sink.frame_format"] == "audio_chat.direct_frame.v1"
    support_ids = {item["id"] for item in config["supports"]}
    assert {"sensor.rgb", "sensor.mic", "actuator.speaker"}.issubset(support_ids)
    assert {"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}} in config["subscriptions"]
    assert {"event": "stream.output.*", "filter": {"stream_type": "actuator.speaker"}} in config["subscriptions"]
    assert {"event": "command.*"} in config["subscriptions"]


def test_ios_phone_registration_event_matches_contract_golden() -> None:
    """测试目标：验证 iOS 注册事件遵守公共 control.device.register.requested 契约。

    测试方法：读取 iOS 专用 golden 和 Swift 源码，检查注册 payload 必备字段。
    预期结果：注册事件携带 user_id、device_id、auth、properties、supports 和 subscriptions，
    不包含 target_device 或固定 phone/glass 路由字段。
    """

    golden = json.loads((ROOT / "testdata/contracts/endpoints/ios_phone_register_requested.json").read_text(encoding="utf-8"))
    payload = golden["payload"]

    assert golden["event_name"] == "control.device.register.requested"
    assert golden["producer_id"] == payload["device_id"]
    assert payload["client_type"] == "ios-phone"
    assert payload["auth"]["mode"] == "disabled"
    assert payload["properties"]["phone.task.find_object_phone_task"] is True
    assert payload["properties"]["phone.task.traffic_light_phone_task"] is True
    assert payload["properties"]["direct.camera_sink"] is True
    assert payload["properties"]["direct.camera_sink.path"] == "/ws/camera"
    assert payload["properties"]["direct.camera_sink.frame_format"] == "audio_chat.direct_frame.v1"
    assert "target_device" not in json.dumps(golden)
    assert "target_device_id" not in json.dumps(golden)
    assert {item["id"] for item in payload["supports"]} == {"sensor.rgb", "sensor.mic", "actuator.speaker"}

    source = _read("AudioChatPhone/Core/AudioChatEndpointRuntime.swift")
    for token in [
        "control.device.register.requested",
        "\"auth\": config.auth.payload",
        "\"properties\": properties",
        "\"supports\": config.supports.map",
        "\"subscriptions\": config.subscriptions.map",
        "direct.camera_sink.uris",
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
    direct_codec = _read("AudioChatPhone/Core/DirectCameraFrameCodec.swift")
    direct_server = _read("AudioChatPhone/Core/DirectCameraSinkServer.swift")
    combined = runtime + codec + direct_codec + direct_server

    required_tokens = [
        "/ws/control",
        "/ws/stream",
        "stream.control.open.requested",
        "stream.output.started",
        "stream.output.finished",
        "stream.output.closed",
        "command.requested",
        "command.accepted",
        "command.progress",
        "command.completed",
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
        "DirectCameraSinkServer",
        "DirectCameraFrameCodec",
        "direct.camera_sink.frame_format",
    ]
    for token in required_tokens:
        assert token in combined

    forbidden_tokens = ["capture_photo", "target_device", "target_device_id", "/api/tasks/report-event"]
    for token in forbidden_tokens:
        assert token not in combined


def test_ios_phone_direct_camera_sink_files_are_part_of_xcode_target() -> None:
    """测试目标：验证 iOS phone 具备 ESP32 相机直连接收入口。

    测试方法：静态检查直连相机接收器、帧编解码器和 Xcode target。
    预期结果：工程能编译这些文件，并且支持 audio-chat 直连帧的 4 字节 header
    长度 + JSON header + JPEG payload 格式。
    """

    project = _read("AudioChatPhone.xcodeproj/project.pbxproj")
    codec = _read("AudioChatPhone/Core/DirectCameraFrameCodec.swift")
    server = _read("AudioChatPhone/Core/DirectCameraSinkServer.swift")
    runtime = _read("AudioChatPhone/Core/AudioChatEndpointRuntime.swift")

    for filename in [
        "IPAddressProvider.swift",
        "DirectCameraFrameCodec.swift",
        "DirectWebSocketFrameParser.swift",
        "DirectCameraSinkServer.swift",
    ]:
        assert filename in project

    for token in [
        "stream_type",
        "sensor.rgb",
        "payload_size",
        "UInt32(headerData.count).bigEndian",
        "/ws/camera",
        "Sec-WebSocket-Accept",
    ]:
        assert token in codec + server

    assert "latestDirectCameraFrame" in runtime
    assert "direct_camera_sink" in runtime
    assert "direct_camera_sent" not in runtime


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
