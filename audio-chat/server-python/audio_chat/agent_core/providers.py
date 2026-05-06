from __future__ import annotations

import os
import queue
import threading
import time
from dataclasses import dataclass
from typing import Iterable, Protocol

from audio_chat.protocol import StreamChunk, new_id


class ProviderUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class AsrProviderConfig:
    provider: str = "mock"
    model: str = "mock-asr"
    allow_mock_fallback: bool = True
    realtime_timeout_seconds: float = 5.0
    max_sentence_silence_ms: int = 800


@dataclass(frozen=True)
class TextModelProviderConfig:
    provider: str = "mock"
    model: str = "mock-text"
    allow_mock_fallback: bool = True


@dataclass(frozen=True)
class TranscriptEvent:
    text: str
    final: bool = False


class AsrProviderAdapter(Protocol):
    provider_name: str
    model: str

    def append_audio(self, chunk: StreamChunk) -> list[TranscriptEvent]: ...

    def cancel(self) -> None: ...


class MockAsrProviderAdapter:
    provider_name = "mock"

    def __init__(self, model: str = "mock-asr", transcript: str = "mock transcript") -> None:
        self.model = model
        self.transcript = transcript
        self._sent_delta = False

    def append_audio(self, chunk: StreamChunk) -> list[TranscriptEvent]:
        events: list[TranscriptEvent] = []
        if not self._sent_delta and chunk.payload:
            self._sent_delta = True
            events.append(TranscriptEvent(text=self.transcript[:4], final=False))
        if chunk.final:
            events.append(TranscriptEvent(text=self.transcript, final=True))
        return events

    def cancel(self) -> None:
        self._sent_delta = False


class DashScopeAsrProviderAdapter:
    provider_name = "dashscope"

    def __init__(
        self,
        model: str,
        *,
        timeout_seconds: float = 5.0,
        max_sentence_silence_ms: int = 800,
    ) -> None:
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            raise ProviderUnavailable("DASHSCOPE_API_KEY is not set; ASR provider downgraded to mock")
        try:
            import dashscope
            from dashscope.audio.asr import Recognition, RecognitionCallback
        except ImportError as exc:
            raise ProviderUnavailable("dashscope package is not installed; ASR provider downgraded to mock") from exc

        self.model = model
        self.timeout_seconds = timeout_seconds
        self._events: queue.Queue[TranscriptEvent] = queue.Queue()
        self._done = threading.Event()
        self._closed = False
        self._final_text = ""
        self._latest_partial = ""
        self._final_sentences: list[str] = []
        self._session_id = new_id("asr")

        adapter = self
        dashscope.api_key = api_key

        class _Callback(RecognitionCallback):
            def on_event(self, result):  # pragma: no cover - exercised in integration
                text, final = _extract_recognition_sentence(result)
                if not text:
                    return
                if final:
                    adapter._final_sentences.append(text)
                    adapter._final_text = "".join(adapter._final_sentences)
                    adapter._latest_partial = ""
                else:
                    adapter._latest_partial = text
                adapter._events.put(TranscriptEvent(text=text, final=final))

            def on_complete(self):  # pragma: no cover - exercised in integration
                if not adapter._final_text and adapter._latest_partial:
                    adapter._final_text = adapter._latest_partial
                adapter._done.set()

            def on_error(self, result):  # pragma: no cover - exercised in integration
                adapter._events.put(TranscriptEvent(text=f"[asr_error] {result}", final=True))
                adapter._done.set()

        self._recognition = Recognition(
            model=model,
            format="pcm",
            sample_rate=16000,
            semantic_punctuation_enabled=False,
            max_sentence_silence=max_sentence_silence_ms,
            callback=_Callback(),
        )
        self._recognition.start()

    def append_audio(self, chunk: StreamChunk) -> list[TranscriptEvent]:
        if self._closed:
            return []
        if chunk.payload:
            self._recognition.send_audio_frame(bytes(chunk.payload))
        events = self._drain_events()
        if chunk.final:
            self._closed = True
            threading.Thread(target=self._recognition.stop, name=f"asr-stop-{self._session_id}", daemon=True).start()
            self._done.wait(timeout=self.timeout_seconds)
            events.extend(self._drain_events())
            final_text = self._final_text or self._latest_partial
            if final_text and not any(event.final for event in events):
                events.append(TranscriptEvent(text=final_text, final=True))
        return events

    def cancel(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._recognition.stop()
        except Exception:
            pass

    def _drain_events(self) -> list[TranscriptEvent]:
        events: list[TranscriptEvent] = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                return events


class TextModelAdapter(Protocol):
    provider_name: str
    model: str

    def stream_text(self, transcript: str) -> Iterable[str]: ...

    def cancel(self) -> None: ...


class MockTextModelAdapter:
    provider_name = "mock"

    def __init__(self, model: str = "mock-text") -> None:
        self.model = model
        self._cancelled = False

    def stream_text(self, transcript: str) -> Iterable[str]:
        self._cancelled = False
        for delta in ("This ", "is ", "a streaming mock response."):
            if self._cancelled:
                return
            yield delta

    def cancel(self) -> None:
        self._cancelled = True


class OpenAICompatibleTextModelAdapter:
    provider_name = "openai-compatible"

    def __init__(self, model: str, *, api_key_env: str = "OPENAI_API_KEY", base_url_env: str = "OPENAI_BASE_URL") -> None:
        api_key = os.getenv(api_key_env)
        if not api_key:
            raise ProviderUnavailable(f"{api_key_env} is not set; text model provider downgraded to mock")
        from openai import OpenAI

        self.model = model
        self._cancelled = False
        self._client = OpenAI(api_key=api_key, base_url=os.getenv(base_url_env) or None)

    def stream_text(self, transcript: str) -> Iterable[str]:
        self._cancelled = False
        stream = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "You are the audio-chat TextAgentCore."},
                {"role": "user", "content": transcript},
            ],
            stream=True,
        )
        for item in stream:
            if self._cancelled:
                return
            delta = item.choices[0].delta.content or ""
            if delta:
                yield delta

    def cancel(self) -> None:
        self._cancelled = True


class DashScopeCompatibleTextModelAdapter(OpenAICompatibleTextModelAdapter):
    provider_name = "dashscope-compatible"

    def __init__(self, model: str) -> None:
        os.environ.setdefault("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        super().__init__(model=model, api_key_env="DASHSCOPE_API_KEY", base_url_env="OPENAI_BASE_URL")


def build_asr_provider(config: AsrProviderConfig) -> tuple[AsrProviderAdapter, str | None]:
    try:
        if config.provider == "mock":
            return MockAsrProviderAdapter(model=config.model), None
        if config.provider == "dashscope":
            return DashScopeAsrProviderAdapter(
                model=config.model,
                timeout_seconds=config.realtime_timeout_seconds,
                max_sentence_silence_ms=config.max_sentence_silence_ms,
            ), None
        raise ProviderUnavailable(f"unsupported ASR provider: {config.provider}")
    except ProviderUnavailable as exc:
        if not config.allow_mock_fallback:
            raise
        return MockAsrProviderAdapter(model="mock-asr"), str(exc)


def build_text_model(config: TextModelProviderConfig) -> tuple[TextModelAdapter, str | None]:
    try:
        if config.provider == "mock":
            return MockTextModelAdapter(model=config.model), None
        if config.provider == "openai-compatible":
            return OpenAICompatibleTextModelAdapter(model=config.model), None
        if config.provider == "dashscope-compatible":
            return DashScopeCompatibleTextModelAdapter(model=config.model), None
        raise ProviderUnavailable(f"unsupported text model provider: {config.provider}")
    except ProviderUnavailable as exc:
        if not config.allow_mock_fallback:
            raise
        return MockTextModelAdapter(model="mock-text"), str(exc)


def _extract_recognition_sentence(result) -> tuple[str, bool]:
    getter = getattr(result, "get_sentence", None)
    if not callable(getter):
        return "", False
    try:
        sentence = getter()
    except Exception:
        return "", False
    if not isinstance(sentence, dict):
        return "", False
    text = sentence.get("text")
    if text is None:
        return "", False
    return str(text), sentence.get("end_time") is not None
