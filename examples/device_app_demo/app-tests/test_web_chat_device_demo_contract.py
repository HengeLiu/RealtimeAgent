from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DEMO_ROOT = ROOT / "examples" / "device_app_demo"
WEB_ROOT = DEMO_ROOT / "web-chat"


def _read(relative: str) -> str:
    return (WEB_ROOT / relative).read_text(encoding="utf-8")


def test_web_chat_uses_local_javascript_device_sdk() -> None:
    """测试目标：确认 Web Chat 验证入口只依赖本仓库 JavaScript Device SDK。

    测试方法：读取 Web Chat 前端入口和 package.json，检查本地 SDK import 与静态服务命令。
    预期结果：页面从 `/devices/javascript/src/` 导入本地 SDK 模块，并从仓库根目录提供静态资源。
    """

    app = _read("app.js")
    package = _read("package.json")

    assert 'from "/devices/javascript/src/device-client.js' in app
    assert 'from "/devices/javascript/src/options.js"' in app
    assert "python3 -m http.server 8766 --directory ../../.." in package
    assert "DeviceClient" in app


def test_web_chat_runtime_enables_sdk_hardware_without_protocol_code() -> None:
    """测试目标：确认 Web Chat 是 JavaScript SDK 验证 App，不承载协议实现。

    测试方法：静态检查 Web Chat 运行时代码。
    预期结果：App 只通过 SDK 启用麦克风、单帧相机和 speaker，并调用 SDK 高层对话 API。
    """

    app = _read("app.js")
    html = _read("index.html")
    source = app + html

    for token in [
        "new DeviceClient({",
        "AudioInput.enabled()",
        "Camera.enabled({source: cameraSource})",
        "Speaker.enabled({",
        "PlaybackBuffer.default()",
        "client.requestPermissions()",
        "client.register()",
        "client.startConversation({reason:",
        "client.requestConversationClose({reason:",
        "client.onDebugLog",
        "client.onConnectionStateChange",
        "client.onConversationStateChange",
        "client.onCustomCommand",
        "BrowserCameraFrameSource",
    ]:
        assert token in source

    for forbidden in [
        "new WebSocket(",
        "/ws/control",
        "/ws/stream/audio/input",
        "/ws/stream/audio/output",
        "/ws/stream/visual/input",
        "control.user.wake.detected",
        "encodeStreamChunk",
        "decodeStreamChunk",
        "navigator.mediaDevices.getUserMedia",
        "echoCancellation",
        "noiseSuppression",
        "autoGainControl",
    ]:
        assert forbidden not in source


def test_web_chat_has_debug_and_browser_permission_surface() -> None:
    """测试目标：确认 Web Chat 提供浏览器实测需要的基础调试入口。

    测试方法：读取 HTML 和运行时代码，检查调试面板、诊断复制、日志清理和 server URL 保存。
    预期结果：页面能展示 diagnostics、保存 server URL，并支持复制排障信息。
    """

    html = _read("index.html")
    app = _read("app.js")

    for token in [
        'id="serverUrl"',
        'id="cameraPreview"',
        'id="debugPanel"',
        'id="diagnosticsText"',
        'id="copyDiagnosticsButton"',
        'id="clearLogsButton"',
        "localStorage.setItem",
        "diagnosticsSnapshot()",
        "navigator.clipboard.writeText",
    ]:
        assert token in html + app
