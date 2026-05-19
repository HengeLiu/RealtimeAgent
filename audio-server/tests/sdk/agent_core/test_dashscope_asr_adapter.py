from __future__ import annotations

import sys
import types

from audio_chat.agent_core.providers import DashScopeAsrProviderAdapter
from audio_chat.protocol import StreamChunk


class _FakeRecognitionResult:
    """测试用 DashScope ASR 回调结果。"""

    def __init__(self, text: str, *, final: bool, sentence: dict | None = None) -> None:
        self.text = text
        self.final = final
        self.sentence = sentence

    def get_sentence(self) -> dict:
        """返回 DashScope SDK 同形 sentence 字典。"""

        if self.sentence is not None:
            return self.sentence
        return {"text": self.text, "end_time": 1 if self.final else None}


class _FakeRecognition:
    """测试用 Recognition，模拟 provider 先回调 final，流关闭时再 complete。"""

    def __init__(self, *, callback, **_) -> None:
        self.callback = callback
        self.sent_final = False

    def start(self) -> None:
        """模拟连接建立。"""

    def send_audio_frame(self, _payload: bytes) -> None:
        """首个音频帧立即产出一次 final sentence。"""

        if not self.sent_final:
            self.sent_final = True
            self.callback.on_event(_FakeRecognitionResult("设置一个1分钟的计时器，到时间后提醒我。", final=True))

    def stop(self) -> None:
        """流关闭时只触发 complete，不应让 adapter 再补一个重复 final。"""

        self.callback.on_complete()


def test_dashscope_asr_does_not_emit_duplicate_final_on_stream_close(monkeypatch) -> None:
    """测试目标：验证 DashScope ASR 已回调 final 后，输入流关闭不会再补发同一 final。

    测试方法：用 fake dashscope Recognition 模拟 `send_audio_frame()` 先产生 final，
    随后发送 `chunk.final=True` 关闭输入流。
    预期结果：两次 append 总共只有一个 final TranscriptEvent。
    """

    dashscope = types.ModuleType("dashscope")
    audio = types.ModuleType("dashscope.audio")
    asr = types.ModuleType("dashscope.audio.asr")
    asr.Recognition = _FakeRecognition
    asr.RecognitionCallback = object
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "dashscope", dashscope)
    monkeypatch.setitem(sys.modules, "dashscope.audio", audio)
    monkeypatch.setitem(sys.modules, "dashscope.audio.asr", asr)

    provider = DashScopeAsrProviderAdapter(model="fake-asr", timeout_seconds=0.2)
    first_events = provider.append_audio(
        StreamChunk(
            user_id="user-asr",
            session_id="sess-asr",
            stream_id="stream-asr",
            stream_type="sensor.mic",
            seq=0,
            payload=b"pcm",
            final=False,
        )
    )
    final_events = provider.append_audio(
        StreamChunk(
            user_id="user-asr",
            session_id="sess-asr",
            stream_id="stream-asr",
            stream_type="sensor.mic",
            seq=1,
            payload=b"",
            final=True,
        )
    )

    all_events = [*first_events, *final_events]
    assert [event.text for event in all_events if event.final] == ["设置一个1分钟的计时器，到时间后提醒我。"]


def test_dashscope_asr_preserves_paraformer_sentence_begin(monkeypatch) -> None:
    """测试目标：验证 Paraformer 的空文本 sentence_begin 不会被 ASR adapter 丢弃。

    测试方法：用 fake Recognition 回调 `sentence_begin=true` 且 `text=""` 的结果。
    预期结果：adapter 返回 TranscriptEvent，并保留 sentence_begin、sentence_id 和 begin_time。
    """

    class BeginRecognition(_FakeRecognition):
        """模拟 Paraformer 先返回句子开始事件。"""

        def send_audio_frame(self, _payload: bytes) -> None:
            """首个音频帧立即产出 sentence_begin。"""

            if not self.sent_final:
                self.sent_final = True
                self.callback.on_event(
                    _FakeRecognitionResult(
                        "",
                        final=False,
                        sentence={
                            "sentence_id": 7,
                            "begin_time": 900,
                            "end_time": None,
                            "text": "",
                            "sentence_begin": True,
                            "sentence_end": False,
                            "words": [],
                        },
                    )
                )

    dashscope = types.ModuleType("dashscope")
    audio = types.ModuleType("dashscope.audio")
    asr = types.ModuleType("dashscope.audio.asr")
    asr.Recognition = BeginRecognition
    asr.RecognitionCallback = object
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "dashscope", dashscope)
    monkeypatch.setitem(sys.modules, "dashscope.audio", audio)
    monkeypatch.setitem(sys.modules, "dashscope.audio.asr", asr)

    provider = DashScopeAsrProviderAdapter(model="fake-asr", timeout_seconds=0.2)
    events = provider.append_audio(
        StreamChunk(
            user_id="user-asr",
            session_id="sess-asr",
            stream_id="stream-asr",
            stream_type="sensor.mic",
            seq=0,
            payload=b"pcm",
            final=False,
        )
    )

    assert len(events) == 1
    assert events[0].text == ""
    assert events[0].sentence_begin is True
    assert events[0].sentence_id == 7
    assert events[0].begin_time_ms == 900
