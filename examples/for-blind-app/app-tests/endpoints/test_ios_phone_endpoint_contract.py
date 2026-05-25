from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
IOS_ROOT = ROOT / "examples" / "for-blind-app" / "devices" / "native-ios-phone"


def _read(relative: str) -> str:
    return (IOS_ROOT / relative).read_text(encoding="utf-8")


def _read_root(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_ios_phone_project_and_config_are_present() -> None:
    """测试目标：验证 iOS phone 参考端已经从协议锚点推进到可打开工程。

    测试方法：检查 Xcode project、共享 scheme、AppConfig 示例和 App 内配置资源。
    预期结果：开发者可以直接打开 `RealtimeAgentPhone.xcodeproj`，并替换 `AppConfig.json`
    进行 Simulator 或真机协议联调。
    """

    assert (IOS_ROOT / "RealtimeAgentPhone.xcodeproj/project.pbxproj").exists()
    assert (IOS_ROOT / "RealtimeAgentPhone.xcodeproj/xcshareddata/xcschemes/RealtimeAgentPhone.xcscheme").exists()
    assert (IOS_ROOT / "RealtimeAgentPhone/RealtimeAgentPhoneApp.swift").exists()
    assert (IOS_ROOT / "RealtimeAgentPhone/Resources/AppConfig.json").exists()
    assert (IOS_ROOT / "AppConfig.example.json").exists()


def test_ios_phone_config_schema_matches_endpoint_protocol() -> None:
    """测试目标：验证 iOS 配置字段符合新版 Device SDK 标准入口。

    测试方法：读取 `AppConfig.example.json`，检查 server、user、device、auth 和显式硬件 enable。
    预期结果：iOS 不要求 App 手写 supports，麦克风、相机、speaker 由 SDK 根据 enable 自动声明。
    """

    config = json.loads((IOS_ROOT / "AppConfig.example.json").read_text(encoding="utf-8"))

    assert config["server_url"].startswith("http://")
    assert config["user_id"]
    assert config["device_id"] == "dev-ios-phone-001"
    assert config["auth"]["mode"] == "disabled"
    assert config["audio_input"]["enabled"] is True
    assert config["camera"]["enabled"] is True
    assert config["speaker"]["enabled"] is True
    assert config["speaker"]["buffer"] == {
        "start_watermark_ms": 600,
        "low_watermark_ms": 3000,
        "high_watermark_ms": 12000,
        "max_buffer_ms": 20000,
    }
    assert "phone.task.find_object_phone_task" not in config.get("properties", {})
    assert "phone.task.traffic_light_phone_task" not in config.get("properties", {})
    assert "supports" not in config


def test_ios_phone_registration_event_uses_protocol_payload_fields() -> None:
    """测试目标：验证 iOS 注册事件遵守当前 control.device.register.requested 字段约定。

    测试方法：读取 AppConfig 示例和 Swift 源码，检查注册 payload 必备字段。
    预期结果：注册事件携带 user_id、device_id、auth 和 properties；supports 由 SDK 根据
    显式硬件 enable 自动生成，不要求 App 配置手写。
    """

    config = json.loads((IOS_ROOT / "AppConfig.example.json").read_text(encoding="utf-8"))
    payload = {
        "device_id": config["device_id"],
        "client_type": "ios-phone",
        "auth": config.get("auth") or {"mode": "disabled"},
        "properties": config.get("properties") or {},
    }
    payload["properties"]["direct.camera_sink"] = True
    payload["properties"]["direct.camera_sink.path"] = "/ws/camera"
    payload["properties"]["direct.camera_sink.frame_format"] = "realtime_agent.direct_frame.v1"

    assert payload["client_type"] == "ios-phone"
    assert payload["auth"]["mode"] == "disabled"
    assert "phone.task.find_object_phone_task" not in payload["properties"]
    assert "phone.task.traffic_light_phone_task" not in payload["properties"]
    assert payload["properties"]["direct.camera_sink"] is True
    assert payload["properties"]["direct.camera_sink.path"] == "/ws/camera"
    assert payload["properties"]["direct.camera_sink.frame_format"] == "realtime_agent.direct_frame.v1"
    assert "target_device" not in json.dumps(payload)
    assert "target_device_id" not in json.dumps(payload)

    runtime = _read("RealtimeAgentPhone/Core/RealtimeAgentEndpointRuntime.swift")
    sdk_device = _read_root("devices/swift/Sources/RealtimeAgentDeviceKit/RealtimeAgentDevice.swift")
    sdk_client = _read_root("devices/swift/Sources/RealtimeAgentDeviceKit/RealtimeAgentDeviceClient.swift")
    sdk_options = _read_root("devices/swift/Sources/RealtimeAgentDeviceKit/RealtimeAgentDeviceOptions.swift")
    sdk_avfoundation = _read_root("devices/swift/Sources/RealtimeAgentDeviceKit/Media/AVFoundationAdapters.swift")
    source = runtime + sdk_device + sdk_client + sdk_options + sdk_avfoundation
    for token in [
        "control.device.register.requested",
        "DeviceClient(",
        "auth: config.auth.payload",
        "properties: properties",
        "applying(audioInput: audioInput, camera: camera, speaker: speaker)",
        "properties[\"realtime_agent.audio_input\"]",
        "properties[\"realtime_agent.audio_output\"]",
        "copy.sensors.append",
        "direct.camera_sink.uris",
        "RealtimeAgentDefaultMicrophoneSource",
        "RealtimeAgentDefaultCameraFrameSource",
        "RealtimeAgentDefaultSpeakerSink",
    ]:
        assert token in source


def test_ios_phone_handles_control_and_stream_events_without_hidden_rpc() -> None:
    """测试目标：验证 iOS 参考端只通过 event / stream 协议处理端侧能力。

    测试方法：静态检查 Swift 代码中的事件名、stream 类型和禁止 RPC 的关键词。
    预期结果：iOS 能处理输出 stream、响应 sensor.rgb 采集请求、上传测试 PCM，
    且没有 `capture_photo` 或固定设备路由字段。
    """

    runtime = _read("RealtimeAgentPhone/Core/RealtimeAgentEndpointRuntime.swift")
    codec = _read("RealtimeAgentPhone/Core/StreamChunkCodec.swift")
    direct_codec = _read("RealtimeAgentPhone/Core/DirectCameraFrameCodec.swift")
    direct_server = _read("RealtimeAgentPhone/Core/DirectCameraSinkServer.swift")
    sdk_client = _read_root("devices/swift/Sources/RealtimeAgentDeviceKit/RealtimeAgentDeviceClient.swift")
    sdk_custom = _read_root("devices/swift/Sources/RealtimeAgentDeviceKit/RealtimeAgentCustomCommandContext.swift")
    sdk_input = _read_root("devices/swift/Sources/RealtimeAgentDeviceKit/RealtimeAgentInputStreamRequest.swift")
    sdk_output = _read_root("devices/swift/Sources/RealtimeAgentDeviceKit/RealtimeAgentOutputStreamSession.swift")
    sdk_buffer = _read_root("devices/swift/Sources/RealtimeAgentDeviceKit/Media/SpeakerPlaybackBuffer.swift")
    combined = runtime + codec + direct_codec + direct_server + sdk_client + sdk_custom + sdk_input + sdk_output + sdk_buffer

    required_tokens = [
        "/ws/control",
        "/ws/stream",
        "stream.control.open.requested",
        "stream.output.started",
        "stream.output.closed",
        "custom.command.requested",
        "onCustomCommand",
        "realtime_agent.custom_command_consumer",
        "realtime_agent.custom_event_subscriptions",
        "RealtimeAgentCustomCommandContext",
        "downstream.pause.requested",
        "downstream.resume.requested",
        "stream.input.opened",
        "stream.input.closed",
        "sensor.rgb",
        "sensor.mic",
        "actuator.speaker",
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
    预期结果：工程能编译这些文件，并且支持 realtime-agent 直连帧的 4 字节 header
    长度 + JSON header + JPEG payload 格式。
    """

    project = _read("RealtimeAgentPhone.xcodeproj/project.pbxproj")
    codec = _read("RealtimeAgentPhone/Core/DirectCameraFrameCodec.swift")
    server = _read("RealtimeAgentPhone/Core/DirectCameraSinkServer.swift")
    runtime = _read("RealtimeAgentPhone/Core/RealtimeAgentEndpointRuntime.swift")

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


def test_ios_phone_declares_hardware_permissions() -> None:
    """测试目标：验证真机运行默认 AVFoundation adapter 所需权限已声明。

    测试方法：读取 Info.plist，检查相机和麦克风用途说明。
    预期结果：iOS 真机启动默认相机和麦克风 adapter 时不会因缺少用途说明崩溃。
    """

    plist = _read("RealtimeAgentPhone/Info.plist")
    assert "NSCameraUsageDescription" in plist
    assert "NSMicrophoneUsageDescription" in plist


def test_ios_phone_readme_documents_simulator_real_device_and_signed_token() -> None:
    """测试目标：验证 README 覆盖可运行客户端的启动和鉴权说明。

    测试方法：检查 README 是否说明 config sync、Simulator build、真机配置和
    signed_token 生成提示。
    预期结果：无 Xcode 环境时 contract test 仍能约束文档，避免只提交代码骨架。
    """

    readme = _read("README.md")
    for token in [
        "RealtimeAgentPhone.xcodeproj",
        "xcodebuild -scheme RealtimeAgentPhone",
        "realtime-agent.config.sync",
        "AppConfig.json",
        "signed_token",
        "sensor.rgb",
        "actuator.speaker",
    ]:
        assert token in readme
