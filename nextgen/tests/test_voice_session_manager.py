"""语音会话管理测试。"""

from __future__ import annotations

import time

from nextgen.apps.server.agent.agent_center import AgentCenter
from nextgen.apps.server.conversation.voice_session_manager import VoiceSessionManager


class _FakeRealtimeAsrSession:
    def __init__(self, callback):
        self.callback = callback
        self.chunks = []
        self.stopped = False

    def send_audio_chunk(self, chunk: bytes) -> None:
        self.chunks.append(chunk)

    def stop(self) -> None:
        self.stopped = True


class _FakeAsrService:
    def __init__(self):
        self.streaming_session = None

    def transcribe_file(self, _audio_path: str) -> str:
        return "帮我找一下手机"

    def start_streaming_session(self, on_sentence, audio_format: str = "pcm"):
        self.streaming_session = _FakeRealtimeAsrSession(callback=on_sentence)
        return self.streaming_session


class _FakeTtsService:
    sample_rate = 16000
    audio_format = "pcm"

    def stream_text(self, text: str, on_audio_chunk):
        on_audio_chunk(f"audio:{text}".encode("utf-8"))
        return f"audio:{text}".encode("utf-8")


class _FakeChatService:
    def stream_reply(self, messages):
        latest = messages[-1]["content"]
        yield "收到，"
        time.sleep(0.1)
        yield f"正在处理：{latest}。"


def _drain_messages(session) -> list[dict]:
    items = []
    while True:
        try:
            items.append(session.outgoing.get_nowait())
        except Exception:
            break
    return items


def test_voice_session_manager_can_process_push_to_talk_audio() -> None:
    """验证对讲模式可以产出 TTS 音频块。"""

    manager = VoiceSessionManager(
        asr_service=_FakeAsrService(),
        tts_service=_FakeTtsService(),
        chat_service=_FakeChatService(),
        agent_center=AgentCenter(),
    )
    session = manager.create_session(device_id="glass-001", mode="push_to_talk")
    transcript = session.process_push_to_talk_audio("/tmp/fake.wav")
    assert transcript == "帮我找一下手机"
    session.current_generation_thread.join(timeout=2)
    messages = _drain_messages(session)
    assert any(item["type"] == "tts.audio.chunk" for item in messages)
    assert any(item["type"] == "tts.done" for item in messages)


def test_voice_session_manager_realtime_can_interrupt_previous_reply() -> None:
    """验证实时模式下新的用户语句会打断上一轮播报。"""

    asr_service = _FakeAsrService()
    manager = VoiceSessionManager(
        asr_service=asr_service,
        tts_service=_FakeTtsService(),
        chat_service=_FakeChatService(),
        agent_center=AgentCenter(),
    )
    session = manager.create_session(device_id="glass-001", mode="realtime")
    session.start_realtime()
    assert asr_service.streaming_session is not None
    asr_service.streaming_session.callback("第一句话", True)
    time.sleep(0.05)
    asr_service.streaming_session.callback("第二句话", True)
    if session.current_generation_thread is not None:
        session.current_generation_thread.join(timeout=2)
    messages = _drain_messages(session)
    assert any(item["type"] == "session.started" for item in messages)
    assert any(item["type"] == "tts.stop" for item in messages)
    assert any(item["type"] == "tts.audio.chunk" for item in messages)
