import os

import pytest

from audio_chat.agent_core.providers import AsrProviderConfig, build_asr_provider
from audio_chat.output import TtsProviderConfig
from audio_chat.output.service import build_tts_provider
from audio_chat.protocol import StreamChunk


pytestmark = pytest.mark.skipif(
    not os.getenv("DASHSCOPE_API_KEY"),
    reason="DASHSCOPE_API_KEY is required for real provider integration tests",
)


def test_dashscope_asr_provider_real_session_can_start() -> None:
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
            payload=b"\x00\x00" * 320,
            final=True,
        )
    )

    assert downgrade is None
    assert provider.provider_name == "dashscope"
    assert isinstance(events, list)


def test_dashscope_streaming_tts_real_session_returns_metrics() -> None:
    provider, downgrade = build_tts_provider(
        TtsProviderConfig(
            provider="dashscope",
            model=os.getenv("AUDIO_CHAT_TTS_MODEL", "cosyvoice-v3-flash"),
            voice=os.getenv("AUDIO_CHAT_TTS_VOICE", "longanhuan"),
            allow_mock_fallback=False,
        )
    )

    first = provider.synthesize_delta("你好")
    provider.finish()
    metrics = provider.metrics()

    assert downgrade is None
    assert provider.provider_name == "dashscope"
    assert isinstance(first, bytes)
    assert metrics["provider"] == "dashscope"
