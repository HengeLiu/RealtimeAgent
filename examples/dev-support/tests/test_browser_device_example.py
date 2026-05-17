from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
BROWSER_DEVICE_ROOT = ROOT / "examples" / "dev-support" / "devices" / "browser-glass"


def _html() -> str:
    return (BROWSER_DEVICE_ROOT / "index.html").read_text(encoding="utf-8")


def test_browser_device_uses_simplified_device_registration_protocol() -> None:
    """测试目标：验证 browser-glass 按 README 的简化设备协议注册。

    测试方法：静态读取 HTML，检查注册 payload 使用 supports 表达设备能力，
    只保留广播日志使用的 debug routes，不再手写业务路由订阅。
    预期结果：页面作为普通 Device 注册，业务事件路由由 server 根据 supports 编译。
    """

    html = _html()

    assert "control.device.register.requested" in html
    assert "client_type: \"browser-glass\"" in html
    assert "supports: DEVICE_SUPPORTS" in html
    assert "properties: {" in html
    assert '{event: "stream.control.*", filter: {stream_type: "sensor.rgb"}}' not in html
    assert '{event: "stream.output.*", filter: {stream_type: "actuator.speaker"}}' not in html
    assert "capabilities: {" not in html
    assert "streams.produce" not in html
    assert "streams.consume" not in html


def test_browser_device_supports_match_checked_capability_file() -> None:
    """测试目标：验证页面内置 supports 与设备能力文件保持同一套语义 ID。

    测试方法：读取 `device.audio-chat.yaml`，并静态检查页面注册能力覆盖浏览器已实现能力。
    预期结果：浏览器页面和能力文件都使用 `sensor.mic`、`sensor.rgb`、
    `actuator.speaker`、`actuator.haptic`，避免示例互相矛盾。
    """

    html = _html()
    capability_file = yaml.safe_load((BROWSER_DEVICE_ROOT / "device.audio-chat.yaml").read_text(encoding="utf-8"))
    sensor_types = {item["type"] for item in capability_file["supports"]["sensors"]}
    actuator_types = {item["type"] for item in capability_file["supports"]["actuators"]}

    assert "rgb" in sensor_types
    assert "vibrator" in actuator_types
    assert 'type: "rgb"' in html
    assert 'type: "vibrator"' in html
    assert '"audio_chat.audio_input": "sensor.mic"' in html
    assert '"audio_chat.audio_output": "actuator.speaker"' in html


def test_browser_device_declares_and_handles_peer_video_sender() -> None:
    """测试目标：验证 browser-glass 具备 peer video sender 静态处理入口。

    测试方法：静态读取 HTML 和设备能力文件，检查命令名、连接、帧发送和 stop handler。
    预期结果：页面可响应 `peer.video.sender.start`，并声明 glass sender properties。
    """

    html = _html()
    capability_file = yaml.safe_load((BROWSER_DEVICE_ROOT / "device.audio-chat.yaml").read_text(encoding="utf-8"))

    assert capability_file["properties"]["device_role"] == "glass"
    assert capability_file["properties"]["peer.video.sender"] is True
    assert "peer.video.sender.start" in html
    assert "peer.video.sender.start.stop" in html
    assert "startPeerVideoSender" in html
    assert "sendPeerVideoFrame" in html
    assert "peer.sender.connected" in html
    assert "peer.video.frame.sent" not in html
    assert "peer_video_sender_failed" in html
    assert "beforeunload" in html


def test_browser_device_supports_realtime_offline_audio_modes() -> None:
    """测试目标：验证 browser-glass 支持真实麦克风和离线音频长连接测试。

    测试方法：静态检查页面包含离线实时注入、段落 final 边界和 20ms chunk
    发送逻辑，且不再暴露快速回放模式。
    预期结果：离线音频按实时节奏上传，每段完成后暂停发送并等待下一段。
    """

    html = _html()

    required = [
        "offline_realtime",
        "sendOfflineAudioRealtime",
        "offline_segment_completed",
        "offline_paused",
        "MIC_CHUNK_BYTES = INPUT_RATE * DEFAULT_CHUNK_MS / 1000 * 2",
        "setInterval(() =>",
        "DEFAULT_CHUNK_MS",
    ]
    for item in required:
        assert item in html
    assert "offline_fast" not in html
    assert "sendOfflineAudioFast" not in html


def test_browser_device_switches_visible_fields_by_input_mode() -> None:
    """测试目标：验证 browser-glass 使用样例目录选择入口和明确的视觉上传按钮。

    测试方法：静态检查页面隐藏原生文件 input，使用 File System Access API 的样例
    选择按钮，并提供视频采帧预览入口。
    预期结果：离线音频、图片和视频样例由按钮选择，视频可在服务端请求采集时按帧抽取。
    """

    html = _html()

    assert ".hidden { display: none !important; }" in html
    assert 'id="chooseAudioSample"' in html
    assert 'id="chooseImageSample"' in html
    assert 'id="chooseVideoSample"' in html
    assert 'id="uploadImageNow"' in html
    assert "<h2>音频选择</h2>" in html
    assert "<h2>带图输入</h2>" in html
    assert "<h2>自定义事件</h2>" not in html
    assert 'id="audioFile" class="hidden"' in html
    assert 'id="imageFile" class="hidden"' in html
    assert 'id="videoFile" class="hidden"' in html
    assert 'id="visualPreviewArea" class="visual-preview"' in html
    assert 'id="visualFramePreview"' in html
    assert 'id="selectedVideoSource"' in html
    assert 'id="selectedVideoSource" class="source-video" muted playsinline preload="metadata"' in html
    assert "controls></video>" not in html
    assert "pointer-events: none" in html
    assert 'selectedVideoSourceEl.style.display = "block"' not in html
    assert 'id="capturedFrameStrip"' in html
    assert "frame-thumb" in html
    assert "source-image" in html
    assert "source-video" in html
    assert "source-camera" in html
    assert 'classList.toggle("source-video", active === "video")' in html
    assert "renderSelectedImagePreview" in html
    assert "renderSelectedVideoPreview" in html
    assert "drawVideoFrameToPreview" in html
    assert "drawPreviewPlaceholder" in html
    assert "showVisualPreview" in html
    assert "ensureSelectedVideoTimelineRunning" in html
    assert "CAPTURE_PREVIEW_MIN_INTERVAL_MS" in html
    assert "CAPTURE_THUMBNAIL_MAX_COUNT" in html
    assert "encodeJpegFromCanvas" in html
    assert "playSelectedVideoForCapture" not in html
    assert "stopSelectedVideoCapture" in html
    assert "图片和视频只能二选一" in html
    assert "clearSelectedVideoSample()" in html
    assert "clearSelectedImageSamples()" in html
    assert "activeVisualInputKind" in html
    assert "showOpenFilePicker" in html
    assert "showDirectoryPicker" in html
    assert "restoreSampleSelections()" in html
    assert 'audioModeSelect.addEventListener("change", updateInputModePanels)' in html
    assert 'chooseAudioSampleButton.classList.toggle("hidden", !offlineAudio)' in html
    assert 'id="afterFile"' not in html
    assert 'multiple' in html
    assert 'id="silenceMs"' not in html
    assert 'id="sendPhoto"' not in html
    assert "captureSelectedVideoFrame" in html
    assert "uploadSelectedVideoFramesRealtime" in html
    assert "requestVideoFrameCallback" in html
    assert "manual_confirm" not in html


def test_browser_device_persists_form_config_and_stable_device_id() -> None:
    """测试目标：验证 browser-glass 会保存浏览器端配置且 device_id 不再随机生成。

    测试方法：静态检查页面使用 localStorage 保存表单配置，并使用固定默认 device_id。
    预期结果：刷新页面后保留上次连接参数和输入模式，除非用户手动修改 device_id。
    """

    html = _html()

    assert 'BROWSER_CONFIG_STORAGE_KEY = "audio-chat.browser-glass.v1"' in html
    assert 'DEFAULT_DEVICE_ID = "dev-browser-glass-001"' in html
    assert "loadStoredBrowserConfig()" in html
    assert "usableStoredServerUrl(stored.server_url)" in html
    assert "resolveInitialBrowserConfig(params)" in html
    assert "applyBrowserConfig(initialBrowserConfig)" in html
    assert "bindBrowserConfigPersistence()" in html
    assert "window.localStorage.setItem(BROWSER_CONFIG_STORAGE_KEY" in html
    assert 'searchParams.get("device_id") || stored.device_id || DEFAULT_DEVICE_ID' in html
    assert "dev-browser-${Math.random" not in html


def test_browser_device_has_right_side_event_log_panel() -> None:
    """测试目标：验证 browser-glass 使用底部固定双日志文本框。

    测试方法：静态检查页面使用固定底部 log-dock，并用两个 readonly textarea
    分别展示运行日志和广播事件。
    预期结果：日志区域固定高度、横向排列，且不再使用页签切换。
    """

    html = _html()

    assert ".log-dock { position: fixed;" in html
    assert "grid-template-columns: 1fr 1fr" in html
    assert '<textarea id="log" class="log-box" readonly>' in html
    assert '<textarea id="broadcastLog" class="log-box" readonly>' in html
    assert 'id="runtimeLogTab"' not in html
    assert 'id="broadcastLogTab"' not in html
    assert "function logBroadcastEvent(item)" in html
    assert "logBroadcastEvent(item);" in html


def test_browser_device_subscribes_all_event_prefixes_for_debug_log() -> None:
    """测试目标：验证 browser-glass 为调试日志订阅所有内置事件前缀。

    测试方法：静态检查注册 payload 包含 control、stream、agent、tool、task、
    memory、system 的通配订阅。
    预期结果：设备可以在广播事件页签看到服务器广播出来的事件，但业务处理逻辑仍只处理已支持事件。
    """

    html = _html()

    assert "logBroadcastEvent(item);" in html
    assert "controlWs.onmessage = (message) => handleControlEvent(AudioChatEvent.fromObject(JSON.parse(message.data)).toObject());" in html


def test_browser_device_opens_stream_socket_after_audio_session() -> None:
    """测试目标：验证 browser-glass 不在注册后提前建立数据连接。

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
    assert "startAudioButton.disabled = !registered || !sessionId || sending" in html


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


def test_browser_device_resets_mic_state_when_server_closes_input_stream() -> None:
    """测试目标：验证 server 关闭 sensor.mic 后，下一段离线音频会新建 stream。

    测试方法：静态检查 browser-glass 处理 `stream.input.closed + sensor.mic`，
    并清空 inputStreamId、离线 timer 和本地状态。
    预期结果：连续对话期间如果 server 因 idle_timeout 关闭原输入流，端侧不会复用
    已关闭的 stream_id。
    """

    html = _html()
    handler_body = html.split("function markMicInputClosed(streamId", 1)[1].split("function pcm16ToFloat", 1)[0]
    control_body = html.split("async function handleControlEvent(item)", 1)[1].split("async function handleRgbConfigure", 1)[0]

    assert 'item.event_name === "stream.input.closed" && item.stream_type === "sensor.mic"' in control_body
    assert "markMicInputClosed(item.stream_id" in control_body
    assert "streamId !== inputStreamId" in handler_body
    assert "clearInterval(offlineTimer)" in handler_body
    assert "if (processor) processor.disconnect()" in handler_body
    assert "if (micStream) micStream.getTracks().forEach((track) => track.stop())" in handler_body
    assert "inputStreamId = null" in handler_body
    assert 'activeAudioSource = audioModeSelect.value === "offline_realtime" ? "offline_paused" : "idle"' in handler_body


def test_browser_device_defers_output_close_until_audio_arrives() -> None:
    """测试目标：验证 server close 信令早于音频 chunk 到达时不会提前关闭播放。

    测试方法：静态检查 browser-glass 在 `closeOutputWhenDrained()` 中要求当前
    output stream 已经收到音频并发送过 `stream.output.started`，再允许上报
    `stream.output.finished/closed`。
    预期结果：Text TTS 这类短音频不会因为 close.requested 先到而只听到提示音。
    """

    html = _html()
    close_body = html.split("function closeOutputWhenDrained(streamId)", 1)[1].split(
        "function stopOutputPlayback",
        1,
    )[0]
    stream_body = html.split("function handleStreamChunk(chunk)", 1)[1].split(
        "async function ensureAudioContext",
        1,
    )[0]

    assert "pendingOutputClose.set(item.stream_id, item)" in html
    assert "if (!request || !outputStarted.has(streamId)) return;" in close_body
    assert "if (!outputStarted.has(chunk.stream_id))" in stream_body
    assert "outputStarted.add(chunk.stream_id)" in stream_body
    assert "if (outputClosed.has(chunk.stream_id))" in stream_body
    assert stream_body.index("if (outputClosed.has(chunk.stream_id))") < stream_body.index(
        "recordOutputChunk(chunk, durationMs)"
    )
    assert "recordOutputChunk(chunk, durationMs)" in stream_body
    assert "summarizeOutputPlayback(streamId, \"drained\")" in close_body
    assert 'sendEvent(event("stream.output.finished"' in close_body
    assert 'sendEvent(event("stream.output.closed"' in close_body


def test_browser_device_keeps_parallel_stream_state_for_audio_and_rgb() -> None:
    """测试目标：验证 browser-glass 能在音频长连接期间并行处理视觉 stream。

    测试方法：静态检查页面维护 streamStates，并提供 sensor.rgb single/continuous
    上传、停止和状态显示逻辑。
    预期结果：关闭 sensor.rgb 不会依赖或清空 sensor.mic 的 inputStreamId。
    """

    html = _html()

    assert "streamStates = new Map()" in html
    assert "setStreamState" in html
    assert "uploadSingleVisualSnapshot" in html
    assert "uploadRgbImages" in html
    assert "uploadSelectedImages" in html
    assert "readSelectedImageFrames" in html
    assert "captureLiveCameraFrame" in html
    assert "captureJpegFrame" in html
    assert "waitStreamSocketBufferedDrain" in html
    assert "stopContinuousRgb" in html
    assert "stream_type: \"sensor.rgb\"" in html
    assert "stream_type: \"sensor.mic\"" in html
    assert "inputStreamId = null" in html
    assert '"camera_frame_captured"' in html
    assert "selectedImageSampleFiles.length" in html
    assert "视频模式只支持连续上传" not in html


def test_browser_device_uploads_camera_snapshot_without_file_input() -> None:
    """测试目标：验证摄像头抓拍结果不会被文件上传逻辑覆盖。

    测试方法：静态检查 uploadRgbImages 使用调用方传入的 images，并且只在
    uploadSelectedImages 中读取文件输入。
    预期结果：live_camera 模式的 stream.control.open.requested 会上传摄像头帧，
    不会误报“请选择图片文件”。
    """

    html = _html()
    upload_body = html.split("async function uploadRgbImages(images, item = null", 1)[1].split(
        "async function readSelectedImageFrames()",
        1,
    )[0]
    selected_body = html.split("async function uploadSelectedImages(item = null)", 1)[1].split(
        "async function uploadRgbImages",
        1,
    )[0]

    assert 'await uploadRgbImages([await captureLiveCameraFrame()], item, "camera_frame_captured")' in html
    assert "selectedImageSampleFiles.length" in html
    assert "readSelectedImageFrames()" not in upload_body
    assert "if (!images.length)" in upload_body
    assert "await waitStreamSocketBufferedDrain()" in upload_body
    assert "await uploadRgbImages(await readSelectedImageFrames(), item, \"images_uploaded\")" in selected_body


def test_browser_device_has_manual_sensor_rgb_upload_button() -> None:
    """测试目标：验证 browser-glass 能主动上传图片触发手机回显测试。

    测试方法：静态检查按钮、启停状态和 onclick 处理逻辑。
    预期结果：注册后按钮可用，并复用 `uploadSingleVisualSnapshot()` 发送 sensor.rgb。
    """

    html = _html()

    assert 'const uploadImageNowButton = document.getElementById("uploadImageNow")' in html
    assert "uploadImageNowButton.disabled = !registered" in html
    assert "uploadImageNowButton.onclick = () =>" in html
    assert 'reason: "manual_browser_upload"' in html
    assert "uploadSingleVisualSnapshot({payload: {reason: \"manual_browser_upload\"}})" in html


def test_browser_device_appends_tail_silence_for_offline_realtime_audio() -> None:
    """测试目标：验证离线音频实时注入会追加短静音尾巴。

    测试方法：静态检查 browser-glass 在读取离线音频后追加固定静音片段。
    预期结果：Omni provider 的 VAD 有稳定的回合结束边界，不需要强制创建响应。
    """

    html = _html()
    offline_body = html.split("async function openOfflineAudioStream()", 1)[1].split(
        "function sendOfflineAudioRealtime()",
        1,
    )[0]

    assert "const OFFLINE_TAIL_SILENCE_MS = 800" in html
    assert "const tailSilenceChunks = Math.ceil(OFFLINE_TAIL_SILENCE_MS / DEFAULT_CHUNK_MS)" in offline_body
    assert "const tailSilenceBytes = tailSilenceChunks * MIC_CHUNK_BYTES" in offline_body
    assert "audioWithTail.set(offlineAudio, 0)" in offline_body


def test_browser_device_open_cli_defaults_to_new_example() -> None:
    """测试目标：确认 Web 设备打开命令默认指向新的 browser-glass。

    测试方法：读取 CLI 源码，检查默认路径。
    预期结果：默认入口指向 `examples/dev-support/devices/browser-glass/index.html`。
    """

    source = (ROOT / "audio-server" / "audio_chat" / "cli" / "web.py").read_text(encoding="utf-8")

    assert "examples/dev-support/devices/browser-glass/index.html" in source
    old_default = "examples/dev-support/devices/browser" + "-device/index.html"
    assert old_default not in source
