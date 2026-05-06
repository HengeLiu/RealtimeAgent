import asyncio
from importlib import resources

from aiohttp import ClientSession, web

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.server import AudioChatHttpServer


def _html() -> str:
    return resources.files("audio_chat.endpoints.web_glass_static").joinpath("index.html").read_text(encoding="utf-8")


def test_web_glass_html_contains_required_webrtc_and_protocol_events() -> None:
    """测试目标：验证 web-glass 页面声明浏览器 AEC 能力和 audio-chat 协议事件。

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
    """测试目标：验证 web-glass JS 实现了 StreamChunkCodec 兼容结构。

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
    assert "function delay(ms)" in html
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
    assert "stopAllOutputPlayback(\"barge_in_local\")" in html
    assert "stream.output.cancel.requested" in html
    assert "stopOutputPlayback(item.stream_id, \"server_cancelled\")" in html
    assert "提交本轮" not in html
    assert "MediaFrame" not in html


def test_web_glass_static_entry_returns_200(tmp_path) -> None:
    """测试目标：验证 `audio-chat.server.run` 暴露 web-glass 静态入口。

    测试方法：启动临时 aiohttp app，请求 `/web-glass`。
    预期结果：返回 200 和 HTML 内容。
    """

    async def run() -> None:
        audio_app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
        server = AudioChatHttpServer(audio_app)
        runner = web.AppRunner(server.create_web_app())
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001
        try:
            async with ClientSession() as session:
                async with session.get(f"http://127.0.0.1:{port}/web-glass") as response:
                    text = await response.text()
                    assert response.status == 200
                    assert "audio-chat web-glass" in text
        finally:
            await runner.cleanup()

    asyncio.run(run())
