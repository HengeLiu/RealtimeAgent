"""百炼 TTS 服务测试。"""

from __future__ import annotations

from nextgen.apps.server.conversation.dashscope_tts_service import DashscopeTtsService


class _FakeResult:
    """模拟 TTS 返回对象。"""

    def __init__(self, audio_data: bytes):
        self._audio_data = audio_data

    def get_audio_data(self) -> bytes:
        return self._audio_data


def test_dashscope_tts_service_falls_back_to_audio_data(monkeypatch) -> None:
    """验证当回调未收到音频块时，会退回到完整音频数据。"""

    captured = []

    def _fake_call(**kwargs):
        return _FakeResult(audio_data=b"pcm-data")

    monkeypatch.setattr(
        "nextgen.apps.server.conversation.dashscope_tts_service.SpeechSynthesizer.call",
        _fake_call,
    )

    service = DashscopeTtsService(api_key="test-key")
    audio = service.stream_text("你好", on_audio_chunk=captured.append)

    assert audio == b"pcm-data"
    assert captured == [b"pcm-data"]
