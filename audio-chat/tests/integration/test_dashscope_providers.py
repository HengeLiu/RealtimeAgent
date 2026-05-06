import os
from pathlib import Path

import pytest

from audio_chat.agent_core.providers import AsrProviderConfig, build_asr_provider
from audio_chat.output import TtsProviderConfig
from audio_chat.output.service import build_tts_provider
from audio_chat.protocol import StreamChunk

EXPECTED_TEXT = "你好"
TESTDATA_DIR = Path("audio-chat/testdata/provider")
ASR_SAMPLE = TESTDATA_DIR / "dashscope-nihao-16k.pcm"
ASR_EXPECTED = TESTDATA_DIR / "dashscope-nihao-expected.txt"

pytestmark = pytest.mark.skipif(
    not os.getenv("DASHSCOPE_API_KEY"),
    reason="DASHSCOPE_API_KEY is required for real provider integration tests",
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
    provider.finish()
    audio = first
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
