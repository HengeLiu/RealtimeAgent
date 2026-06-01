from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
DEMO_ROOT = ROOT / "examples" / "device_demo"
IOS_ROOT = DEMO_ROOT / "ios"


def _read(relative: str) -> str:
    return (DEMO_ROOT / relative).read_text(encoding="utf-8")


def test_device_demo_ios_project_uses_local_swift_device_sdk() -> None:
    """测试目标：确认 Swift Device SDK 验证入口只依赖本仓库 SDK。

    测试方法：读取 DeviceDemo Xcode 工程，检查本地 Swift Package 引用和 target。
    预期结果：工程引用 `../../../devices/swift`，不引用外部业务 App 或其他工作区。
    """

    project = _read("ios/DeviceDemo.xcodeproj/project.pbxproj")

    assert "DeviceDemo" in project
    assert "RealtimeAgentDeviceKit" in project
    assert "XCLocalSwiftPackageReference \"../../../devices/swift\"" in project
    assert "relativePath = ../../../devices/swift;" in project
    assert "AIGlass" not in project
    assert "OpenAIglassesDemo/devices/swift" not in project


def test_device_demo_runtime_enables_sdk_hardware_without_business_app_code() -> None:
    """测试目标：确认 DeviceDemo 是 SDK 验证 App，不承载外部业务逻辑。

    测试方法：静态检查 Swift 运行时、调试日志和硬件 enable 配置。
    预期结果：App 只通过 `DeviceClient` 启用麦克风、单帧相机和 speaker，并调用 SDK 高层对话 API。
    """

    runtime = _read("ios/DeviceDemo/DeviceDemoRuntime.swift")
    content = _read("ios/DeviceDemo/ContentView.swift")
    source = runtime + content

    for token in [
        "import RealtimeAgentDeviceKit",
        "DeviceClient(",
        "audioInput: .enabled()",
        "camera: .enabled(",
        "modes: [\"single\"]",
        "speaker: .enabled(",
        "duplexMode: .fullDuplexServerBargeIn",
        "client.requestPermissions()",
        "client.register()",
        "client.startConversation(reason:",
        "client.requestConversationClose(reason:",
        "onDebugLog",
        "DeviceDemo.log",
    ]:
        assert token in source

    for forbidden in [
        "import AVFoundation",
        "AIGlass",
        "RealtimeAgentPhone",
        "DirectCameraSinkServer",
        "requestMediaPermissions",
        "sendWakeDetected",
        "client.sendEvent(",
        "control.user.wake.detected",
        "phone.task.",
        "target_device",
        "target_device_id",
        "modes: [\"single\", \"continuous\"]",
    ]:
        assert forbidden not in source


def test_device_demo_server_config_is_independent_from_external_business_app() -> None:
    """测试目标：确认真机 SDK 验证使用独立 server 配置。

    测试方法：读取 `examples/device_demo/agent-server/server.yaml`。
    预期结果：配置名为 `device_demo`，使用真实 provider，禁用 mock fallback，
    不加载外部业务能力。
    """

    config = yaml.safe_load(_read("agent-server/server.yaml"))

    assert config["app-name"] == "device_demo"
    assert config["agent"]["mode"] == "omni"
    assert config["agent"]["omni"]["provider"] == "qwen"
    assert config["agent"]["vision"]["provider"] == "dashscope-compatible"
    assert config["agent"]["vision"]["asr_provider"] == "dashscope"
    assert config["agent"]["vision"]["tts_provider"] == "dashscope"
    assert config["agent"]["vision"]["allow_mock_fallback"] is False
    assert config["tools"]["discover"]["packages"] == []
    assert config["tasks"]["enabled"] is False


def test_device_demo_declares_required_ios_permissions() -> None:
    """测试目标：确认真机运行需要的 iOS 权限声明仍然存在。

    测试方法：读取 DeviceDemo 的 Info.plist。
    预期结果：相机、麦克风和局域网权限文案齐全。
    """

    plist = _read("ios/DeviceDemo/Info.plist")

    assert "NSCameraUsageDescription" in plist
    assert "NSMicrophoneUsageDescription" in plist
    assert "NSLocalNetworkUsageDescription" in plist


def test_swift_default_audio_adapter_enables_voice_processing_and_interruptions() -> None:
    """测试目标：确认 Swift 默认音频适配器启用系统语音处理，并默认允许播放期间用户打断。

    测试方法：静态检查 AVFoundation 默认适配器里的音频会话模式、输入节点 voice processing 和麦克风策略。
    预期结果：默认麦克风和 speaker 共用 `.voiceChat` 会话，并默认继续上传麦克风以支持打断。
    """

    adapter = (ROOT / "devices/swift/Sources/RealtimeAgentDeviceKit/Media/AVFoundationAdapters.swift").read_text(
        encoding="utf-8"
    )
    microphone = (ROOT / "devices/swift/Sources/RealtimeAgentDeviceKit/Media/MicrophoneStreamer.swift").read_text(
        encoding="utf-8"
    )

    assert "mode: .voiceChat" in adapter
    assert "setVoiceProcessingEnabled(true)" in adapter
    assert "microphoneDuringSpeakerPlayback: MicrophoneDuringSpeakerPlayback = .allowInterruptions" in microphone
    assert "speakerPlaybackWarmupMuteMS: Int = 500" in microphone
    assert "microphoneDuringSpeakerPlayback == .muteDuringSpeakerPlayback" in adapter
    assert "isBuiltInSpeakerRoute()" in adapter
    assert "shouldMuteMicrophoneForSpeakerPlayback" in adapter
    assert "Data(repeating: 0" in adapter
    assert "RealtimeAgentVoiceConversationEngine.shared" in adapter
    assert "final class RealtimeAgentVoiceConversationEngine" in adapter
    assert "private let voiceEngine = RealtimeAgentVoiceConversationEngine.shared" in adapter
    assert "configureVoiceConversation" in adapter
