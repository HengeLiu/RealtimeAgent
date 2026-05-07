from __future__ import annotations

import os
import queue
import threading
import time
import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Protocol

from audio_chat.protocol import StreamChunk, new_id


class ProviderUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderCallDiagnostic:
    """真实 provider 调用诊断。

    主要功能：把 provider、model、endpoint、timeout、retry 和 fallback policy
    统一记录成可测试结构，避免真实服务偶发失败时只看到模糊异常。
    主要属性：`ok` 表示调用是否成功，`error` 保存最后一次失败原因。
    """

    provider: str
    model: str
    endpoint: str
    timeout_seconds: float
    max_retries: int
    fallback_policy: str
    attempts: int
    ok: bool
    elapsed_ms: int
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        """返回可写入 JSON 报告的诊断字典。"""

        return {
            "provider": self.provider,
            "model": self.model,
            "endpoint": self.endpoint,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "fallback_policy": self.fallback_policy,
            "attempts": self.attempts,
            "ok": self.ok,
            "elapsed_ms": self.elapsed_ms,
            "error": self.error,
        }


def run_provider_call_with_policy(
    *,
    provider: str,
    model: str,
    endpoint: str,
    timeout_seconds: float,
    max_retries: int,
    allow_mock_fallback: bool,
    operation: Callable[[], Any],
) -> tuple[Any | None, ProviderCallDiagnostic]:
    """按统一稳定性策略执行一次真实 provider 调用。

    主要逻辑：同步执行 `operation`，失败后按 `max_retries` 重试；每次调用后检查
    总耗时是否超过 `timeout_seconds`，并把 provider、model、endpoint、timeout、
    retry 和 fallback policy 写入诊断对象。
    参数：provider/model/endpoint 用于定位外部服务；`operation` 为实际调用。
    返回值：调用结果和诊断对象；失败时结果为 None。
    异常情况：本函数不抛出 provider 异常，由调用方根据 `diagnostic.ok` 和
    fallback policy 决定降级或失败。
    """

    started = time.monotonic()
    attempts = 0
    last_error = ""
    max_attempts = max(1, int(max_retries) + 1)
    for _ in range(max_attempts):
        attempts += 1
        try:
            result = operation()
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if timeout_seconds > 0 and elapsed_ms > int(timeout_seconds * 1000):
                raise TimeoutError(f"provider call exceeded timeout {timeout_seconds}s")
            return result, ProviderCallDiagnostic(
                provider=provider,
                model=model,
                endpoint=endpoint,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
                fallback_policy="mock" if allow_mock_fallback else "fail",
                attempts=attempts,
                ok=True,
                elapsed_ms=elapsed_ms,
            )
        except Exception as exc:  # noqa: BLE001 - provider SDK 会抛出自定义异常
            last_error = f"{type(exc).__name__}: {exc}"
            if timeout_seconds > 0 and time.monotonic() - started >= timeout_seconds:
                break
    return None, ProviderCallDiagnostic(
        provider=provider,
        model=model,
        endpoint=endpoint,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        fallback_policy="mock" if allow_mock_fallback else "fail",
        attempts=attempts,
        ok=False,
        elapsed_ms=int((time.monotonic() - started) * 1000),
        error=last_error,
    )


@dataclass(frozen=True)
class AsrProviderConfig:
    provider: str = "mock"
    model: str = "mock-asr"
    allow_mock_fallback: bool = True
    realtime_timeout_seconds: float = 5.0
    max_sentence_silence_ms: int = 800
    max_retries: int = 1


@dataclass(frozen=True)
class TextModelProviderConfig:
    provider: str = "mock"
    model: str = "mock-text"
    allow_mock_fallback: bool = True
    request_timeout_seconds: float = 5.0
    max_retries: int = 1


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

    def __init__(
        self,
        model: str,
        *,
        api_key_env: str = "OPENAI_API_KEY",
        base_url_env: str = "OPENAI_BASE_URL",
        request_timeout_seconds: float = 5.0,
        max_retries: int = 1,
    ) -> None:
        api_key = os.getenv(api_key_env)
        if not api_key:
            raise ProviderUnavailable(f"{api_key_env} is not set; text model provider downgraded to mock")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ProviderUnavailable("openai package is not installed; text model provider downgraded to mock") from exc

        self.model = model
        self.request_timeout_seconds = request_timeout_seconds
        self.max_retries = max_retries
        self.endpoint = os.getenv(base_url_env) or "https://api.openai.com/v1"
        self._cancelled = False
        self._client = OpenAI(
            api_key=api_key,
            base_url=os.getenv(base_url_env) or None,
            timeout=request_timeout_seconds,
            max_retries=max_retries,
        )

    def stream_text(self, transcript: str) -> Iterable[str]:
        for item in self.stream_messages(messages=[{"role": "user", "content": transcript}], tools=[]):
            if isinstance(item, str):
                yield item

    def stream_messages(self, *, messages: list[dict], tools: list[dict]) -> Iterable[str | dict]:
        """按 OpenAI-compatible chat completions 协议流式生成文本和工具调用。

        主要逻辑：
        1. 普通 `content` delta 立即 yield，保证 TTS 可以边生成边播放。
        2. `tool_calls` delta 按 index/id/name/arguments 聚合，stream 结束后 yield
           SDK 内部统一的 `{"type": "tool_call", ...}` 结构。
        3. arguments 片段按 JSON 解析，解析失败时保留原始字符串，方便日志排查。

        参数：`messages` 为已包含 tool 回填的对话历史；`tools` 为 provider schema。
        返回值：字符串文本 delta 或内部 tool_call 字典。
        异常情况：provider SDK 异常向上传递，由调用方决定是否降级。
        """

        self._cancelled = False
        stream = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "You are the audio-chat TextAgentCore."},
                *messages,
            ],
            tools=tools or None,
            stream=True,
        )
        pending_tool_calls: dict[int, dict[str, str]] = {}
        for item in stream:
            if self._cancelled:
                return
            choice = item.choices[0]
            delta = choice.delta
            text_delta = getattr(delta, "content", None) or ""
            if text_delta:
                yield text_delta
            for tool_call in getattr(delta, "tool_calls", None) or []:
                index = int(getattr(tool_call, "index", 0) or 0)
                record = pending_tool_calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                call_id = getattr(tool_call, "id", None)
                if call_id:
                    record["id"] = str(call_id)
                function = getattr(tool_call, "function", None)
                if function is not None:
                    name = getattr(function, "name", None)
                    arguments = getattr(function, "arguments", None)
                    if name:
                        record["name"] = str(name)
                    if arguments:
                        record["arguments"] += str(arguments)
        for index in sorted(pending_tool_calls):
            record = pending_tool_calls[index]
            arguments_text = record.get("arguments", "")
            try:
                arguments = json.loads(arguments_text) if arguments_text else {}
            except json.JSONDecodeError:
                arguments = {"_raw_arguments": arguments_text}
            yield {
                "type": "tool_call",
                "id": record.get("id") or f"tool_call_{index}",
                "name": record.get("name") or "",
                "arguments": arguments,
            }

    def cancel(self) -> None:
        self._cancelled = True


class DashScopeCompatibleTextModelAdapter(OpenAICompatibleTextModelAdapter):
    provider_name = "dashscope-compatible"

    def __init__(self, model: str, *, request_timeout_seconds: float = 5.0, max_retries: int = 1) -> None:
        os.environ.setdefault("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        super().__init__(
            model=model,
            api_key_env="DASHSCOPE_API_KEY",
            base_url_env="OPENAI_BASE_URL",
            request_timeout_seconds=request_timeout_seconds,
            max_retries=max_retries,
        )


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
            return OpenAICompatibleTextModelAdapter(
                model=config.model,
                request_timeout_seconds=config.request_timeout_seconds,
                max_retries=config.max_retries,
            ), None
        if config.provider == "dashscope-compatible":
            return DashScopeCompatibleTextModelAdapter(
                model=config.model,
                request_timeout_seconds=config.request_timeout_seconds,
                max_retries=config.max_retries,
            ), None
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
