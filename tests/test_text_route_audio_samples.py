import asyncio
import json
from pathlib import Path

from aiohttp import web

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.server import AudioChatHttpServer
from audio_chat_python_glass.playback import NetworkPythonPlaybackEndpoint, PythonPlaybackEndpoint, load_wav_audio


AUDIO_SAMPLE_ROOT = Path("testdata/audio-sample/wav")


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
    tool_trace = _read_jsonl(session_dir / "tool-trace.jsonl")
    model_events = (session_dir / "model-events.jsonl").read_text(encoding="utf-8")

    assert result["passed"] is True
    assert result["input_audio"]["chunk_count"] > 1
    assert any(item["role"] == "user" and item["content"] == "帮我查一下我眼镜的状态" for item in messages)
    assert any(item["tool_name"] == "query_device_state" and item["ok"] is True for item in tool_trace)
    assert any("当前有 1 台设备在线" in str(item.get("content")) for item in messages)
    assert "assistant_audio.delta" in model_events


def test_text_route_capture_photo_tool_collects_rgb_asset_from_python_glass(tmp_path: Path) -> None:
    """测试目标：验证 text 模型路线中的视觉工具仍遵守 stream/event 协议。

    测试方法：使用“看一下我前面有什么”音频样例触发 `capture_photo`；Python glass 收到
    `stream.control.open.requested` 后上传一帧 `sensor.rgb` 资产。
    预期结果：工具调用成功，资产缓存写入照片，最终文本回复来自工具结果而不是固定图片。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), agent_mode="text"))
    endpoint = PythonPlaybackEndpoint(app=app, user_id="user-text-photo", device_id="dev-text-photo")
    audio = load_wav_audio(AUDIO_SAMPLE_ROOT / "看一下我前面有什么.wav")

    result = endpoint.run_once(audio=audio)

    session_dir = app.recorder.session_dir(result["session_id"])
    messages = _read_jsonl(session_dir / "messages.jsonl")
    tool_trace = _read_jsonl(session_dir / "tool-trace.jsonl")
    assets = _read_jsonl(session_dir / "assets.jsonl")

    assert result["passed"] is True
    assert result["asset_uploads"]
    assert any(item["tool_name"] == "capture_photo" and item["ok"] is True for item in tool_trace)
    assert any(item.get("event") == "asset.stored" and item.get("stream_type") == "sensor.rgb" for item in assets)
    assert any("我已经拿到当前照片" in str(item.get("content")) for item in messages)


def test_text_route_network_python_glass_replays_audio_sample_over_websocket(tmp_path: Path) -> None:
    """测试目标：验证 text 路线可以通过真实 HTTP/WebSocket 端测完成无头自动化。

    测试方法：启动 aiohttp server，NetworkPythonPlaybackEndpoint 通过 control/stream 两条
    WebSocket 上传 AudioSample WAV。
    预期结果：网络端测断言通过，输入音频被按多 chunk 上传，并镜像旧 `sessions/<id>`
    产物以兼容迁移期验收脚本。
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
        assert (tmp_path / "runs" / "sessions" / result["session_id"] / "events.jsonl").exists()
        assert (tmp_path / "runs" / "user-text-network" / result["session_id"] / "model-request.json").exists()

    asyncio.run(run())
