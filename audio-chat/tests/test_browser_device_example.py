from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BROWSER_DEVICE_ROOT = ROOT / "device-examples" / "browser-device"


def _html() -> str:
    return (BROWSER_DEVICE_ROOT / "index.html").read_text(encoding="utf-8")


def test_browser_device_uses_simplified_device_registration_protocol() -> None:
    """测试目标：验证 browser-device 按 README 的简化设备协议注册。

    测试方法：静态读取 HTML，检查注册 payload 使用 name、subscriptions 和 properties，
    不再通过 capabilities 表达路由能力。
    预期结果：页面作为普通 Device 注册，事件路由只依赖 subscriptions。
    """

    html = _html()

    assert "control.device.register.requested" in html
    assert "client_type: \"browser-device\"" in html
    assert "properties: {" in html
    assert "subscriptions: [" in html
    assert "capabilities: {" not in html
    assert "streams.produce" not in html
    assert "streams.consume" not in html


def test_browser_device_supports_realtime_offline_audio_modes() -> None:
    """测试目标：验证 browser-device 支持真实麦克风和离线音频长连接测试。

    测试方法：静态检查页面包含离线实时注入、快速回放、静音保持和 20ms chunk
    发送逻辑。
    预期结果：离线音频可以模拟实时 sensor.mic 长连接，快速回放被单独标识。
    """

    html = _html()

    required = [
        "offline_realtime",
        "offline_fast",
        "keep_open_silence",
        "keep_open_idle",
        "sendOfflineAudioRealtime",
        "sendOfflineAudioFast",
        "silenceChunk",
        "MIC_CHUNK_BYTES = INPUT_RATE * DEFAULT_CHUNK_MS / 1000 * 2",
        "setInterval(() =>",
        "DEFAULT_CHUNK_MS",
    ]
    for item in required:
        assert item in html


def test_browser_device_opens_stream_socket_after_audio_session() -> None:
    """测试目标：验证 browser-device 不在注册后提前建立数据连接。

    测试方法：静态检查注册成功分支只启用控制按钮，不创建 WebSocket；
    数据连接由 ensureStreamSocketOpen 在开始音频或视觉 stream 时按需打开。
    预期结果：control ws 是设备级常驻连接，stream ws 是连续对话级连接。
    """

    html = _html()
    registered_branch = html.split('if (item.event_name === "control.device.registered")', 1)[1].split(
        '} else if (item.event_name === "control.audio_session.open.requested")',
        1,
    )[0]

    assert "enableRegisteredControls();" in registered_branch
    assert "new WebSocket(streamUrl)" not in registered_branch
    assert "openStreamSocket();" in html
    assert "closeStreamSocket(\"audio_session_closed\")" in html
    assert "startAudioButton.disabled = !registered || !sessionId || running" in html


def test_browser_device_stop_audio_keeps_dialog_connection() -> None:
    """测试目标：验证停止音频只关闭当前麦克风 stream，不结束连续对话连接。

    测试方法：静态检查停止麦克风逻辑使用 audio_segment_closed 关闭 sensor.mic，
    且 closeStreamSocket 只出现在 audio session 关闭分支。
    预期结果：停止一段音频后可以继续上传下一段，结束连续对话才释放连接。
    """

    html = _html()
    stop_mic_body = html.split("async function stopMic()", 1)[1].split("connectButton.onclick", 1)[0]

    assert "结束连续对话" in html
    assert "audio_segment_closed" in stop_mic_body
    assert "closeStreamSocket(" not in stop_mic_body
    assert "closeStreamSocket(\"audio_session_closed\")" in html
    assert "wakeButton.disabled = !registered || dialogOpen" in html
    assert "closeButton.disabled = !dialogOpen" in html


def test_browser_device_keeps_parallel_stream_state_for_audio_and_rgb() -> None:
    """测试目标：验证 browser-device 能在音频长连接期间并行处理视觉 stream。

    测试方法：静态检查页面维护 streamStates，并提供 sensor.rgb single/continuous
    上传、停止和状态显示逻辑。
    预期结果：关闭 sensor.rgb 不会依赖或清空 sensor.mic 的 inputStreamId。
    """

    html = _html()

    assert "streamStates = new Map()" in html
    assert "setStreamState" in html
    assert "captureRgbAsset" in html
    assert "startContinuousRgb" in html
    assert "stopContinuousRgb" in html
    assert "stream_type: \"sensor.rgb\"" in html
    assert "stream_type: \"sensor.mic\"" in html
    assert "rgbContinuousStreamId = null" in html
    assert "inputStreamId = null" in html


def test_browser_device_open_cli_defaults_to_new_example() -> None:
    """测试目标：确认 Web 设备打开命令默认指向新的 browser-device。

    测试方法：读取 CLI 源码，检查默认路径。
    预期结果：旧 web-glass 仍可通过显式路径使用，但默认入口已经切到新实现。
    """

    source = (ROOT / "server-python" / "audio_chat" / "cli" / "web.py").read_text(encoding="utf-8")

    assert "device-examples/browser-device/index.html" in source
    assert "endpoints-examples/web-glass/index.html" not in source
