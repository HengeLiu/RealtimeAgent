"""阿里百炼 ASR 服务封装。"""

from __future__ import annotations

import os
import queue
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from dashscope.audio.asr import Recognition
from dashscope.audio.asr.recognition import RecognitionCallback, RecognitionResult


def _extract_sentence_text(sentence) -> str:
    """从百炼 ASR 句子对象中提取文本。"""

    if sentence is None:
        return ""
    if isinstance(sentence, dict):
        return str(
            sentence.get("text")
            or sentence.get("sentence")
            or sentence.get("content")
            or ""
        ).strip()
    return str(sentence).strip()


class _NoopRecognitionCallback(RecognitionCallback):
    """同步文件转写使用的空回调。"""

    def on_open(self):
        pass

    def on_complete(self):
        pass

    def on_error(self, _response):
        pass

    def on_close(self):
        pass

    def on_event(self, _result):
        pass


class _StreamingRecognitionCallback(RecognitionCallback):
    """实时 ASR 回调。"""

    def __init__(self, on_sentence: Callable[[str, bool], None]) -> None:
        self.on_sentence = on_sentence

    def on_open(self):
        pass

    def on_complete(self):
        pass

    def on_error(self, _response):
        pass

    def on_close(self):
        pass

    def on_event(self, result: RecognitionResult):
        sentence_payload = result.get_sentence()
        if sentence_payload is None:
            return
        sentences = sentence_payload if isinstance(sentence_payload, list) else [sentence_payload]
        for sentence in sentences:
            text = _extract_sentence_text(sentence)
            if not text:
                continue
            is_final = RecognitionResult.is_sentence_end(sentence)
            self.on_sentence(text, is_final)


@dataclass
class DashscopeAsrService:
    """阿里百炼 ASR 服务。

    说明：
    - 对讲模式使用 `transcribe_file`
    - 实时模式使用 `start_streaming_session`
    """

    api_key: str | None = None
    file_model: str = "fun-asr-realtime-2026-02-28"
    realtime_model: str = "fun-asr-realtime-2026-02-28"
    sample_rate: int = 16000

    def __post_init__(self) -> None:
        if self.api_key is None:
            self.api_key = os.getenv("DASHSCOPE_API_KEY")

    def _build_recognition(self, model: str, callback: RecognitionCallback, audio_format: str) -> Recognition:
        if not self.api_key:
            raise RuntimeError("未配置 DASHSCOPE_API_KEY，无法调用百炼 ASR。")
        return Recognition(
            model=model,
            callback=callback,
            format=audio_format,
            sample_rate=self.sample_rate,
            api_key=self.api_key,
        )

    def transcribe_file(self, audio_path: str, audio_format: str = "wav") -> str:
        """转写一个本地音频文件。"""

        recognition = self._build_recognition(
            model=self.file_model,
            callback=_NoopRecognitionCallback(),
            audio_format=audio_format,
        )
        result = recognition.call(audio_path)
        sentence_payload = result.get_sentence()
        if sentence_payload is None:
            return ""
        sentences = sentence_payload if isinstance(sentence_payload, list) else [sentence_payload]
        texts = [_extract_sentence_text(sentence) for sentence in sentences]
        return "".join([text for text in texts if text]).strip()

    def start_streaming_session(
        self,
        on_sentence: Callable[[str, bool], None],
        audio_format: str = "pcm",
    ) -> "StreamingAsrSession":
        """启动一个实时 ASR 会话。"""

        callback = _StreamingRecognitionCallback(on_sentence=on_sentence)
        recognition = self._build_recognition(
            model=self.realtime_model,
            callback=callback,
            audio_format=audio_format,
        )
        recognition.start()
        return StreamingAsrSession(recognition=recognition)


@dataclass
class StreamingAsrSession:
    """实时 ASR 会话句柄。"""

    recognition: Recognition
    pending_partials: "queue.Queue[str]" = field(default_factory=queue.Queue)

    def send_audio_chunk(self, chunk: bytes) -> None:
        """发送一段音频块。"""

        self.recognition.send_audio_frame(chunk)

    def stop(self) -> None:
        """结束实时 ASR 会话。"""

        self.recognition.stop()
