import os
import importlib.util
from pathlib import Path

import pytest

from audio_chat.agent_core.providers import AsrProviderConfig, TextModelProviderConfig, build_asr_provider, build_text_model
from audio_chat.agent_core.realtime import QwenOmniRealtimeAdapter, RealtimeProviderCallbacks, RealtimeProviderConfig
from audio_chat.output import TtsProviderConfig
from audio_chat.output.service import build_tts_provider
from audio_chat.protocol import StreamChunk

EXPECTED_TEXT = "你好"
TESTDATA_DIR = Path(__file__).resolve().parents[2] / "testdata/provider"
ASR_SAMPLE = TESTDATA_DIR / "dashscope-nihao-16k.pcm"
ASR_EXPECTED = TESTDATA_DIR / "dashscope-nihao-expected.txt"

pytestmark = pytest.mark.skipif(
    not os.getenv("DASHSCOPE_API_KEY") or importlib.util.find_spec("dashscope") is None,
    reason="DASHSCOPE_API_KEY and dashscope package are required for real provider integration tests",
)


def test_dashscope_asr_provider_transcribes_expected_sample() -> None:
    if not ASR_SAMPLE.exists():
        _generate_asr_sample()
    expected = ASR_EXPECTED.read_text(encoding="utf-8").strip()
    provider, downgrade = build_asr_provider(
        AsrProviderConfig(
            provider="dashscope",
            model=os.getenv("AUDIO_CHAT_ASR_MODEL", "paraformer-realtime-v2"),
            allow_mock_fallback=False,
            realtime_timeout_seconds=2,
        )
    )

    events = provider.append_audio(
        StreamChunk(
            user_id="integration-user",
            session_id="integration-session",
            stream_id="integration-stream",
            stream_type="sensor.mic",
            seq=0,
            payload=ASR_SAMPLE.read_bytes(),
            final=True,
        )
    )

    assert downgrade is None
    assert provider.provider_name == "dashscope"
    transcript = "".join(event.text for event in events if event.final)
    assert expected in transcript


def test_dashscope_streaming_tts_real_session_returns_pcm_and_metrics() -> None:
    provider, downgrade = build_tts_provider(
        TtsProviderConfig(
            provider="dashscope",
            model=os.getenv("AUDIO_CHAT_TTS_MODEL", "cosyvoice-v3-flash"),
            voice=os.getenv("AUDIO_CHAT_TTS_VOICE", "longanhuan"),
            sample_rate_hz=16000,
            allow_mock_fallback=False,
        )
    )

    first = provider.synthesize_delta(EXPECTED_TEXT)
    audio = first + provider.finish()
    while True:
        try:
            audio += provider._audio.get_nowait()
        except Exception:
            break
    metrics = provider.metrics()

    assert downgrade is None
    assert provider.provider_name == "dashscope"
    assert audio
    assert metrics["provider"] == "dashscope"
    assert metrics["sample_rate_hz"] == 16000
    assert "tts_first_audio_latency_ms" in metrics


def test_dashscope_compatible_text_model_smoke_returns_text_delta() -> None:
    """测试目标：验证真实 DashScope OpenAI-compatible Text provider 可定位 smoke。

    测试方法：使用 `DASHSCOPE_API_KEY` 构建 text model，发送一条短消息并读取首个文本 delta。
    预期结果：返回非空文本；失败时 pytest 输出 provider、model、endpoint 和 timeout。
    """

    model = os.getenv("AUDIO_CHAT_TEXT_MODEL", "qwen-plus")
    provider, downgrade = build_text_model(
        TextModelProviderConfig(
            provider="dashscope-compatible",
            model=model,
            allow_mock_fallback=False,
            request_timeout_seconds=10,
            max_retries=1,
        )
    )

    try:
        first_delta = next(iter(provider.stream_text("请只回复：ok")))
    except Exception as exc:  # noqa: BLE001 - 集成测试需要保留 provider SDK 原始错误
        endpoint = getattr(provider, "endpoint", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        pytest.fail(
            f"provider=dashscope-compatible model={model} endpoint={endpoint} timeout=10 "
            f"fallback_policy=fail error={type(exc).__name__}: {exc}"
        )

    assert downgrade is None
    assert provider.provider_name == "dashscope-compatible"
    assert first_delta


def test_qwen_omni_realtime_provider_smoke_opens_and_closes_session() -> None:
    """测试目标：验证 Omni Realtime provider 真实会话最小 smoke。

    测试方法：用 DashScope SDK 打开 Qwen Omni realtime 会话，收到打开事件后立即关闭。
    预期结果：至少记录一个 provider 事件；失败报告 provider、model、endpoint 和 timeout。
    """

    records: list[dict] = []
    config = RealtimeProviderConfig(
        provider="qwen",
        model=os.getenv("AUDIO_CHAT_REALTIME_MODEL", "qwen3.5-omni-plus-realtime"),
        voice=os.getenv("AUDIO_CHAT_REALTIME_VOICE", "Tina"),
    )
    provider = QwenOmniRealtimeAdapter(config)
    callbacks = RealtimeProviderCallbacks(
        audio_delta=lambda _audio, _format, _metadata: None,
        audio_done=lambda _metadata: None,
        provider_event=records.append,
        error=lambda message, metadata: records.append({"event": "provider.error", "message": message, **metadata}),
    )

    try:
        provider.open(user_id="integration-user", session_id="integration-omni", callbacks=callbacks)
        provider.close(user_id="integration-user", reason="smoke_done")
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"provider=qwen model={config.model} endpoint={config.websocket_url} timeout=provider_sdk "
            f"fallback_policy=fail error={type(exc).__name__}: {exc}"
        )

    assert records


def _generate_asr_sample() -> None:
    TESTDATA_DIR.mkdir(parents=True, exist_ok=True)
    provider, downgrade = build_tts_provider(
        TtsProviderConfig(
            provider="dashscope",
            model=os.getenv("AUDIO_CHAT_TTS_MODEL", "cosyvoice-v3-flash"),
            voice=os.getenv("AUDIO_CHAT_TTS_VOICE", "longanhuan"),
            sample_rate_hz=16000,
            allow_mock_fallback=False,
        )
    )
    pcm = provider.synthesize_delta(EXPECTED_TEXT)
    provider.finish()
    while True:
        try:
            pcm += provider._audio.get_nowait()
        except Exception:
            break
    assert pcm
    ASR_SAMPLE.write_bytes(pcm)
    ASR_EXPECTED.write_text(EXPECTED_TEXT, encoding="utf-8")
