from pathlib import Path


def _html() -> str:
    return Path(__file__).resolve().parents[1].joinpath("device-examples/browser-glass/index.html").read_text(encoding="utf-8")


def test_web_glass_html_contains_required_webrtc_and_protocol_events() -> None:
    """测试目标：验证 browser-glass 页面声明浏览器 AEC 能力和 audio-chat 协议事件。

    测试方法：读取静态 HTML，检查 getUserMedia 约束和关键控制事件名。
    预期结果：页面包含 AEC/NS/AGC、注册、唤醒、输入打开和输出回执事件。
    """
    html = _html()

    required = [
        "echoCancellation: true",
        "noiseSuppression: true",
        "autoGainControl: true",
        "control.device.register.requested",
        "control.user.wake.detected",
        "stream.input.opened",
        "stream.output.started",
        "stream.output.closed",
    ]
    for item in required:
        assert item in html


def test_web_glass_stream_chunk_codec_shape_is_protocol_compatible() -> None:
    """测试目标：验证 browser-glass JS 实现了 StreamChunkCodec 兼容结构。

    测试方法：检查 JS 中必须写入 4 字节 header_len、JSON header 和 payload，并校验
    `payload_size`。
    预期结果：HTML 包含对应编解码结构，不回退旧 MediaFrame。
    """
    html = _html()

    assert "function encodeStreamChunk" in html
    assert "function decodeStreamChunk" in html
    assert "setUint32(0, headerBytes.length, false)" in html
    assert "JSON.stringify(header)" in html
    assert "out.set(headerBytes, 4)" in html
    assert "out.set(new Uint8Array(chunk.payload), 4 + headerBytes.length)" in html
    assert "getUint32(0, false)" in html
    assert "payload.byteLength !== header.payload_size" in html
    assert "MIC_CHUNK_BYTES = INPUT_RATE * DEFAULT_CHUNK_MS / 1000 * 2" in html
    assert "while (micPcmBuffer.byteLength >= MIC_CHUNK_BYTES)" in html
    assert "sendMicPayload(micPcmBuffer.slice(0, MIC_CHUNK_BYTES))" in html
    assert "final: false" in html
    assert 'function sendFinalMicPayload(reason = "audio_segment_closed")' in html
    assert "final: true" in html
    assert "send final sensor.mic bytes=" in html
    assert 'sendFinalMicPayload("offline_segment_completed")' in html
    assert "control.device.heartbeat.received" in html
    assert "function startHeartbeat()" in html
    assert "close_mode: \"close_after_reply\"" in html
    assert "function delay(ms)" in html
    assert "function defaultServerUrl()" in html
    assert "function normalizeServerUrl(raw)" in html
    assert 'url.hostname === "0.0.0.0"' in html
    assert 'url.hostname = "127.0.0.1"' in html
    assert '"http://127.0.0.1:8765"' in html
    assert "await ensureStreamSocketOpen()" in html
    assert "await delay(120)" in html
    assert "audio session already active; ignore duplicate open request" in html
    assert "wakeButton.disabled = true" in html
    assert "control.user.interrupt.detected" in html
    assert "BARGE_IN_RMS_THRESHOLD = 0.055" in html
    assert "BARGE_IN_REQUIRED_FRAMES = 8" in html
    assert "BARGE_IN_MAX_PEAK = 0.72" in html
    assert "barge-in detected rms=" in html
    assert "peak=${level.peak.toFixed(4)}" in html
    assert "recv audio chunk bytes=" in html
    assert "duration_ms=${durationMs}" in html
    assert "audioContext.createGain()" in html
    assert "if (!outputStarted.has(chunk.stream_id))" in html
    assert "stopAllOutputPlayback(\"barge_in_local\")" in html
    assert "stream.output.cancel.requested" in html
    assert "stopOutputPlayback(item.stream_id, \"server_cancelled\")" in html
    assert "control websocket open timeout" in html
    assert "control ws closed code=${evt.code}" in html
    assert "connect failed: ${err.message}" in html
    assert "提交本轮" not in html
    assert "MediaFrame" not in html


def test_web_glass_sensor_rgb_uses_valid_stream_format() -> None:
    """测试目标：验证 browser-glass 抓拍上传的 JPEG stream 能通过服务端格式校验。

    测试方法：读取静态 HTML，检查 `sensor.rgb` 打开事件和 JPEG chunk 不再使用
    `sample_rate=0`、`chunk_ms=0` 或 `duration_ms=0`，并在打开 stream 后短暂等待。
    预期结果：浏览器端先发送合法的 `stream.input.opened`，再上传 JPEG 数据，避免
    服务端拒绝打开事件后出现 `unknown stream_id`。
    """
    html = _html()

    assert 'format: {codec: "jpeg", sample_rate: 1, channels: 1, chunk_ms: 1}' in html
    assert "codec: \"jpeg\",\n            sample_rate: 1" in html
    assert "duration_ms: 1" in html
    assert "await delay(120);" in html
    assert "streamWs.send(encodeStreamChunk({" in html
    assert 'format: {codec: "jpeg", sample_rate: 0' not in html
    assert "codec: \"jpeg\",\n            sample_rate: 0" not in html
    assert "duration_ms: 0" not in html


def test_web_glass_is_not_served_by_sdk_server() -> None:
    """测试目标：验证 browser-glass 不作为 SDK server 内置静态路由。

    测试方法：检查 server 源码不包含 `/browser-glass` 路由和 `web_glass` handler。
    预期结果：server 只暴露协议和 debug API，不预判具体端侧类型。
    """
    server_source = Path(__file__).resolve().parents[1].joinpath("audio-chat-sdk/audio_chat/server.py").read_text(
        encoding="utf-8"
    )

    assert '"/browser-glass"' not in server_source
    assert "def web_glass" not in server_source
