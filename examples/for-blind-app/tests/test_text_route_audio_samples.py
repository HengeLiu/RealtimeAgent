import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path

from aiohttp import web

from audio_chat import AssetRef
from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.server import AudioChatHttpServer
from audio_chat.tools import ToolExecutor
from audio_chat_python_glass.playback import NetworkPythonPlaybackEndpoint, PythonPlaybackEndpoint, load_wav_audio


AUDIO_SAMPLE_ROOT = Path("testdata/audio-sample")
APP_ROOT = Path("examples/for-blind-app/audio-server")


def _clear_capability_modules() -> None:
    """清理测试进程中缓存的应用能力模块。"""

    for name in list(sys.modules):
        if name == "capabilities" or name.startswith("capabilities."):
            sys.modules.pop(name, None)


def _build_for_blind_text_app(tmp_path: Path, monkeypatch) -> AudioChatApp:
    """创建加载 for-blind-app 能力的 Text 路线应用。

    测试目标：让应用级 `capture_photo` Tool 通过真实自动发现进入注册表。
    测试方法：读取 for-blind-app 的 server.yaml，并把运行产物路径替换到临时目录。
    预期结果：Text 模型路线可调用应用级视觉 Tool，而 SDK 默认应用不暴露该 Tool。
    """

    _clear_capability_modules()
    monkeypatch.syspath_prepend(str(APP_ROOT))
    config = AudioChatConfig.from_yaml(APP_ROOT / "server.yaml")
    return AudioChatApp(
        replace(
            config,
            runs_root=str(tmp_path / "runs"),
            asset_root=str(tmp_path / "runs" / "assets"),
            agent_mode="text",
            asr_provider="mock",
            asr_model="mock-asr",
            text_provider="mock",
            text_model="mock-text",
            tts_provider="mock",
            tts_model="mock-tts",
            tts_voice="mock",
            allow_mock_fallback=True,
        )
    )


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_text_route_uses_audio_sample_filename_as_mock_asr_transcript_and_calls_device_tool(tmp_path: Path) -> None:
    """测试目标：验证 text 模型路线可以用真实 AudioSample 音频完成自动化回放。

    测试方法：Python glass 无头端测上传当前 WAV 样例；mock ASR 根据 WAV 文件名
    生成转写文本，mock text model 按转写触发 `query_device_state` 工具。
    预期结果：ASR、TextAgentCore、ToolGateway、流式 TTS 和 speaker 输出全部产生
    可回放产物，工具轨迹中包含设备状态查询。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), agent_mode="text"))
    endpoint = PythonPlaybackEndpoint(app=app, user_id="user-text-sample", device_id="dev-text-sample")
    audio = load_wav_audio(AUDIO_SAMPLE_ROOT / "帮我查一下我眼镜的状态.wav")

    result = endpoint.run_once(audio=audio)

    session_dir = app.recorder.session_dir(result["session_id"])
    messages = _read_jsonl(session_dir / "messages.jsonl")
    tool_trace = _read_jsonl(session_dir / "tool-events.jsonl")
    model_events = (session_dir / "model-events.jsonl").read_text(encoding="utf-8")

    assert result["passed"] is True
    assert result["input_audio"]["chunk_count"] > 1
    assert any(item["role"] == "user" and item["content"] == "帮我查一下我眼镜的状态" for item in messages)
    assert any(item["tool_name"] == "query_device_state" and item["ok"] is True for item in tool_trace)
    assert any(
        item.get("role") == "tool" and (item.get("content") or {}).get("data", {}).get("count") == 1
        for item in messages
    )
    assert "assistant_audio.delta" in model_events


def test_text_route_capture_photo_tool_collects_rgb_asset_from_python_glass(tmp_path: Path, monkeypatch) -> None:
    """测试目标：验证 text 模型路线中的视觉工具仍遵守 stream/event 协议。

    测试方法：使用“看一下我前面有什么”音频样例触发 `capture_photo`；Python glass 收到
    `stream.control.open.requested` 后上传一帧 `sensor.rgb` 资产。
    预期结果：工具调用成功，资产缓存写入照片，最终文本回复来自工具结果而不是固定图片。
    """

    app = _build_for_blind_text_app(tmp_path, monkeypatch)
    endpoint = PythonPlaybackEndpoint(app=app, user_id="user-text-photo", device_id="dev-text-photo")
    audio = load_wav_audio(AUDIO_SAMPLE_ROOT / "看一下我前面有什么.wav")

    result = endpoint.run_once(audio=audio)

    session_dir = app.recorder.session_dir(result["session_id"])
    messages = _read_jsonl(session_dir / "messages.jsonl")
    tool_trace = _read_jsonl(session_dir / "tool-events.jsonl")
    assets = _read_jsonl(session_dir / "assets.jsonl")

    assert result["asset_uploads"]
    assert any(item["tool_name"] == "capture_photo" and item["ok"] is True for item in tool_trace)
    assert any(item.get("event") == "asset.stored" and item.get("stream_type") == "sensor.rgb" for item in assets)
    assert any(
        item.get("role") == "tool" and (item.get("content") or {}).get("data", {}).get("captured") is True
        for item in messages
    )


def test_capture_photo_uses_browser_camera_cold_start_timeout(monkeypatch) -> None:
    """测试目标：确认抓拍工具默认等待时间能覆盖浏览器摄像头冷启动。

    测试方法：通过 ToolExecutor 执行 `capture_photo`，不传 `timeout_seconds`，
    用假的 RGB facade 记录最终传给 `sensor.rgb.one()` 的超时值。
    预期结果：默认超时为 15 秒，避免首次 `getUserMedia` 超过 5 秒时误判拍照失败。
    """

    _clear_capability_modules()
    monkeypatch.syspath_prepend(str(APP_ROOT))
    from capabilities.tools import CapturePhotoTool

    class FakeRgb:
        def __init__(self) -> None:
            self.timeout_seconds: float | None = None

        async def one(self, *, params: dict, timeout_seconds: float) -> AssetRef:
            self.timeout_seconds = timeout_seconds
            return AssetRef(
                asset_id="asset-test",
                user_id="user-test",
                session_id="sess-test",
                stream_type="sensor.rgb",
                mime_type="image/jpeg",
                created_at_ms=0,
                uri="/tmp/asset-test.jpg",
            )

    class FakeSensors:
        def __init__(self, rgb: FakeRgb) -> None:
            self.rgb = rgb

    class FakeDevices:
        def __init__(self, rgb: FakeRgb) -> None:
            self.sensors = FakeSensors(rgb)

    class FakeContext:
        def __init__(self, rgb: FakeRgb) -> None:
            self.devices = FakeDevices(rgb)

    rgb = FakeRgb()
    result = asyncio.run(ToolExecutor().execute(CapturePhotoTool(), FakeContext(rgb), {}))

    assert result.ok is True
    assert rgb.timeout_seconds == 15


def test_text_route_network_python_glass_replays_audio_sample_over_websocket(tmp_path: Path) -> None:
    """测试目标：验证 text 路线可以通过真实 HTTP/WebSocket 端测完成无头自动化。

    测试方法：启动 aiohttp server，NetworkPythonPlaybackEndpoint 通过 control/stream 两条
    WebSocket 上传 AudioSample WAV。
    预期结果：网络端测断言通过，输入音频被按多 chunk 上传，并镜像`sessions/<id>` 产物用于验收脚本。
    """

    async def run() -> None:
        audio_app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), agent_mode="text"))
        server = AudioChatHttpServer(audio_app)
        runner = web.AppRunner(server.create_web_app())
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001 - 测试读取临时端口
        try:
            endpoint = NetworkPythonPlaybackEndpoint(
                server_url=f"http://127.0.0.1:{port}",
                user_id="user-text-network",
                device_id="dev-text-network",
                runs_root=str(tmp_path / "runs"),
            )
            audio = load_wav_audio(AUDIO_SAMPLE_ROOT / "帮我查一下我眼镜的状态.wav")
            result = await endpoint.run_once(audio=audio)
        finally:
            await runner.cleanup()

        assert result["passed"] is True
        assert result["transport"] == "network"
        assert result["input_audio"]["chunk_count"] > 1
        assert (tmp_path / "runs" / "user-text-network" / result["session_id"] / "events.jsonl").exists()
        assert (tmp_path / "runs" / "user-text-network" / result["session_id"] / "model-request.json").exists()

    asyncio.run(run())
