from __future__ import annotations

import os
import queue
import threading
import time
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

from realtime_agent.protocol import StreamChunk, new_id


class ProviderUnavailable(RuntimeError):
    pass


VISION_AGENT_SYSTEM_PROMPT = (
    "你是中文语音助手。请用简短口语回答用户。"
    "历史助手消息中如果出现 `<用户打断>`，表示标记前的内容用户可能已经听到，"
    "标记后的内容是系统已经生成但未继续播报给用户的上下文，只能作为后续回答参考。"
)


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
    semantic_punctuation_enabled: bool = False
    punctuation_prediction_enabled: bool = True
    disfluency_removal_enabled: bool = True
    inverse_text_normalization_enabled: bool = True
    heartbeat: bool = True
    max_retries: int = 1


@dataclass(frozen=True)
class VisionModelProviderConfig:
    provider: str = "mock"
    model: str = "mock-vision"
    prompt: str = VISION_AGENT_SYSTEM_PROMPT
    allow_mock_fallback: bool = True
    request_timeout_seconds: float = 5.0
    max_retries: int = 1


@dataclass(frozen=True)
class TranscriptEvent:
    """实时 ASR 事件。

    主要功能：承载 provider 返回的文本增量、final 文本以及 Paraformer 这类
    ASR/VAD 合一模型给出的句子边界。VisionRealtimeAgentCore 基于这些结构化字段生成
    内部 speech_started / speech_stopped 事件。
    """

    text: str
    final: bool = False
    sentence_id: int | None = None
    sentence_begin: bool = False
    sentence_end: bool = False
    begin_time_ms: int | None = None
    end_time_ms: int | None = None
    words: list[dict] | None = None
    raw: dict | None = None


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
        transcript = self._transcript_for_chunk(chunk)
        if not self._sent_delta and chunk.payload:
            self._sent_delta = True
            events.append(TranscriptEvent(text=transcript[:4], final=False))
        if chunk.final:
            events.append(TranscriptEvent(text=transcript, final=True))
        return events

    def cancel(self) -> None:
        self._sent_delta = False

    def _transcript_for_chunk(self, chunk: StreamChunk) -> str:
        """根据回放音频元数据生成 mock 转写文本。

        主要逻辑：自动化回放会把 WAV 路径写入 `metadata.source_path`，mock ASR
        使用文件名作为转写结果，这样测试可以直接复用 AudioSample，而不是维护另一套
        文本脚本。没有路径时保留构造函数传入的默认文本。
        参数：`chunk` 为端侧上传的麦克风 chunk。
        返回值：用于后续 VisionRealtimeAgentCore 的转写文本。
        异常情况：路径解析失败时回退默认文本。
        """

        source_path = str((chunk.metadata or {}).get("source_path") or "").strip()
        if not source_path:
            return self.transcript
        stem = Path(source_path).stem.strip()
        return stem or self.transcript


class DashScopeAsrProviderAdapter:
    provider_name = "dashscope"

    def __init__(
        self,
        model: str,
        *,
        timeout_seconds: float = 5.0,
        max_sentence_silence_ms: int = 800,
        semantic_punctuation_enabled: bool = False,
        punctuation_prediction_enabled: bool = True,
        disfluency_removal_enabled: bool = True,
        inverse_text_normalization_enabled: bool = True,
        heartbeat: bool = True,
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
        self._emitted_final_text = False
        self._session_id = new_id("asr")

        adapter = self
        dashscope.api_key = api_key

        class _Callback(RecognitionCallback):
            def on_event(self, result):  # pragma: no cover - exercised in integration
                event = _extract_recognition_event(result)
                if event is None:
                    return
                text = event.text
                final = event.final
                if final:
                    adapter._final_sentences.append(text)
                    adapter._final_text = "".join(adapter._final_sentences)
                    adapter._latest_partial = ""
                    adapter._emitted_final_text = True
                elif text:
                    adapter._latest_partial = text
                adapter._events.put(event)

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
            semantic_punctuation_enabled=semantic_punctuation_enabled,
            max_sentence_silence=max_sentence_silence_ms,
            punctuation_prediction_enabled=punctuation_prediction_enabled,
            disfluency_removal_enabled=disfluency_removal_enabled,
            inverse_text_normalization_enabled=inverse_text_normalization_enabled,
            heartbeat=heartbeat,
            callback=_Callback(),
        )
        self._recognition.start()

    def append_audio(self, chunk: StreamChunk) -> list[TranscriptEvent]:
        if self._closed:
            return []
        if chunk.payload:
            self._send_audio_payload(bytes(chunk.payload), final=chunk.final)
        events = self._drain_events()
        if chunk.final:
            self._closed = True
            threading.Thread(target=self._recognition.stop, name=f"asr-stop-{self._session_id}", daemon=True).start()
            self._done.wait(timeout=self.timeout_seconds)
            events.extend(self._drain_events())
            final_text = self._final_text or self._latest_partial
            if final_text and not self._emitted_final_text and not any(event.final for event in events):
                events.append(TranscriptEvent(text=final_text, final=True))
                self._emitted_final_text = True
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

    def _send_audio_payload(self, payload: bytes, *, final: bool) -> None:
        """向 DashScope ASR 发送音频数据。

        主要逻辑：真实 Paraformer realtime 更稳定的输入形态是连续小帧。端侧实时上行
        本来就是小 chunk，但 provider 测试和离线回放可能一次传入完整 final PCM。
        这里在 adapter 内拆帧，避免一帧大音频紧接 stop 时 provider 还没开始产出识别事件。
        参数：`payload` 为 16k 单声道 PCM，`final` 表示当前输入是否为最终片段。
        返回值：无。
        异常情况：底层 SDK 发送失败时向上传播。
        """

        frame_size = 16000 * 2 // 10
        if not final or len(payload) <= frame_size:
            self._recognition.send_audio_frame(payload)
            if final:
                for _ in range(6):
                    self._recognition.send_audio_frame(b"\x00" * frame_size)
                    time.sleep(0.02)
            return
        for offset in range(0, len(payload), frame_size):
            self._recognition.send_audio_frame(payload[offset : offset + frame_size])
            time.sleep(0.02)
        for _ in range(6):
            self._recognition.send_audio_frame(b"\x00" * frame_size)
            time.sleep(0.02)


class VisionModelAdapter(Protocol):
    provider_name: str
    model: str

    def stream_text(self, transcript: str) -> Iterable[str]: ...

    def cancel(self) -> None: ...


class MockVisionModelAdapter:
    provider_name = "mock"

    def __init__(self, model: str = "mock-vision", *, prompt: str = VISION_AGENT_SYSTEM_PROMPT) -> None:
        self.model = model
        self.prompt = prompt
        self._cancelled = False

    def stream_text(self, transcript: str) -> Iterable[str]:
        for item in self.stream_messages(messages=[{"role": "user", "content": transcript}], tools=[]):
            if not isinstance(item, str):
                continue
            yield item

    def stream_messages(self, *, messages: list[dict], tools: list[dict]) -> Iterable[str | dict]:
        """按Vision 链路语义生成可预测的 mock 模型输出。

        主要逻辑：
        1. 普通问答按短文本 delta 流式返回，覆盖 TTS streaming。
        2. 如果用户转写文本命中设备状态、拍照或计时器意图，并且对应 Tool 已注册，
           先返回统一 tool_call，让 VisionRealtimeAgentCore 走真实 ToolGateway。
        3. 第二轮收到 tool 结果后，基于工具结果生成最终文本。

        参数：`messages` 是当前轮对话历史，`tools` 是可用工具 schema。
        返回值：字符串 delta 或内部 tool_call dict。
        异常情况：本 mock 不抛 provider 异常。
        """

        self._cancelled = False
        tool_message = self._last_tool_message(messages)
        if tool_message is not None:
            for delta in self._reply_after_tool(tool_message):
                if self._cancelled:
                    return
                yield delta
            return

        transcript = self._last_user_text(messages)
        tool_names = self._tool_names(tools)
        tool_call = self._maybe_tool_call(transcript, tool_names)
        if tool_call is not None:
            yield tool_call
            return

        for delta in self._plain_response(transcript):
            if self._cancelled:
                return
            yield delta

    def cancel(self) -> None:
        self._cancelled = True

    @staticmethod
    def _last_user_text(messages: list[dict]) -> str:
        for item in reversed(messages):
            if item.get("role") == "user":
                return str(item.get("content") or "")
        return ""

    @staticmethod
    def _last_tool_message(messages: list[dict]) -> dict | None:
        for item in reversed(messages):
            if item.get("role") == "tool":
                return item
        return None

    @staticmethod
    def _tool_names(tools: list[dict]) -> set[str]:
        names: set[str] = set()
        for item in tools or []:
            function = item.get("function") if isinstance(item, dict) else None
            name = function.get("name") if isinstance(function, dict) else None
            if name:
                names.add(str(name))
        return names

    def _maybe_tool_call(self, transcript: str, tool_names: set[str]) -> dict | None:
        text = transcript.strip()
        if not text:
            return None
        if "query_device_state" in tool_names and any(keyword in text for keyword in ("设备", "眼镜的状态", "在线")):
            return {
                "type": "tool_call",
                "id": "call_mock_query_device_state",
                "name": "query_device_state",
                "arguments": {},
            }
        if "capture_photo" in tool_names and any(keyword in text for keyword in ("前面", "眼前", "看一下", "照片", "画面")):
            return {
                "type": "tool_call",
                "id": "call_mock_capture_photo",
                "name": "capture_photo",
                "arguments": {"timeout_seconds": 5, "freshness_seconds": 0},
            }
        if "start_timer_task" in tool_names and any(keyword in text for keyword in ("计时", "提醒", "分钟", "秒")):
            return {
                "type": "tool_call",
                "id": "call_mock_start_timer_task",
                "name": "start_timer_task",
                "arguments": {"seconds": self._timer_seconds(text), "auto_fire": False},
            }
        return None

    @staticmethod
    def _timer_seconds(text: str) -> int:
        if "三分钟" in text or "3分钟" in text:
            return 180
        if "一分钟" in text or "1分钟" in text:
            return 60
        if "五分钟" in text or "5分钟" in text:
            return 300
        return 60

    @staticmethod
    def _plain_response(transcript: str) -> tuple[str, ...]:
        text = transcript.strip()
        if not text:
            return ("我没有听清，", "请再说一遍。")
        if "你是谁" in text or "自我介绍" in text:
            return ("我是 realtime-agent ", "Vision 链路助手。")
        return ("我听到了：", text, "。")

    @staticmethod
    def _reply_after_tool(tool_message: dict) -> tuple[str, ...]:
        name = str(tool_message.get("name") or "")
        content = tool_message.get("content")
        if not isinstance(content, dict):
            return ("工具已经返回，", "我会继续处理。")
        data = content.get("data") if isinstance(content.get("data"), dict) else {}
        if name == "query_device_state":
            count = data.get("count", 0)
            return (f"当前有 {count} 台设备在线。",)
        if name == "capture_photo":
            if content.get("ok"):
                return ("我已经拿到当前照片。",)
            return ("没有拿到当前照片。",)
        if name == "task_runtime_manager":
            task_id = data.get("task_id") or "新的计时器"
            return (f"计时器已创建，任务编号是 {task_id}。",)
        if name.startswith("start_") and name.endswith("_task"):
            task_id = data.get("task_id") or "新的任务"
            return (f"任务已启动，编号是 {task_id}。",)
        return (str(content.get("message") or "工具调用完成。"),)


class OpenAICompatibleVisionModelAdapter:
    provider_name = "openai-compatible"

    def __init__(
        self,
        model: str,
        *,
        api_key_env: str = "OPENAI_API_KEY",
        base_url_env: str = "OPENAI_BASE_URL",
        request_timeout_seconds: float = 5.0,
        max_retries: int = 1,
        prompt: str = VISION_AGENT_SYSTEM_PROMPT,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        api_key = os.getenv(api_key_env)
        if not api_key:
            raise ProviderUnavailable(f"{api_key_env} is not set; vision model provider downgraded to mock")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ProviderUnavailable("openai package is not installed; vision model provider downgraded to mock") from exc

        self.model = model
        self.prompt = prompt
        self.request_timeout_seconds = request_timeout_seconds
        self.max_retries = max_retries
        self.endpoint = os.getenv(base_url_env) or "https://api.openai.com/v1"
        self.extra_body = dict(extra_body or {})
        self._cancelled = False
        self._active_stream: Any | None = None
        self._stream_lock = threading.RLock()
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
        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": getattr(self, "prompt", VISION_AGENT_SYSTEM_PROMPT)},
                *messages,
            ],
            "tools": tools or None,
            "stream": True,
        }
        extra_body = getattr(self, "extra_body", {})
        if extra_body:
            request_kwargs["extra_body"] = dict(extra_body)
        stream = self._client.chat.completions.create(**request_kwargs)
        if not hasattr(self, "_stream_lock"):
            self._stream_lock = threading.RLock()
        with self._stream_lock:
            self._active_stream = stream
        pending_tool_calls: dict[int, dict[str, str]] = {}
        try:
            for item in stream:
                if self._cancelled:
                    return
                choice = item.choices[0]
                delta = choice.delta
                vision_delta = getattr(delta, "content", None) or ""
                if vision_delta:
                    yield vision_delta
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
        finally:
            with self._stream_lock:
                if self._active_stream is stream:
                    self._active_stream = None
        if self._cancelled:
            return
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
        with self._stream_lock:
            stream = self._active_stream
        close = getattr(stream, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    def request_options_snapshot(self) -> dict[str, Any]:
        """返回当前 provider 请求选项快照。

        主要逻辑：只暴露影响模型行为的稳定字段，写入 `model-request.json` 用于
        排查延迟、thinking 模式和兼容接口参数是否生效。
        参数：无。
        返回值：请求选项字典。
        异常情况：无。
        """

        return {
            "endpoint": self.endpoint,
            "request_timeout_seconds": self.request_timeout_seconds,
            "max_retries": self.max_retries,
            "extra_body": dict(getattr(self, "extra_body", {})),
        }


class DashScopeCompatibleVisionModelAdapter(OpenAICompatibleVisionModelAdapter):
    provider_name = "dashscope-compatible"

    def __init__(
        self,
        model: str,
        *,
        request_timeout_seconds: float = 5.0,
        max_retries: int = 1,
        prompt: str = VISION_AGENT_SYSTEM_PROMPT,
    ) -> None:
        os.environ.setdefault("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        super().__init__(
            model=model,
            api_key_env="DASHSCOPE_API_KEY",
            base_url_env="OPENAI_BASE_URL",
            request_timeout_seconds=request_timeout_seconds,
            max_retries=max_retries,
            prompt=prompt,
            extra_body={"enable_thinking": False},
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
                semantic_punctuation_enabled=config.semantic_punctuation_enabled,
                punctuation_prediction_enabled=config.punctuation_prediction_enabled,
                disfluency_removal_enabled=config.disfluency_removal_enabled,
                inverse_text_normalization_enabled=config.inverse_text_normalization_enabled,
                heartbeat=config.heartbeat,
            ), None
        raise ProviderUnavailable(f"unsupported ASR provider: {config.provider}")
    except ProviderUnavailable as exc:
        if not config.allow_mock_fallback:
            raise
        return MockAsrProviderAdapter(model="mock-asr"), str(exc)


def build_vision_model(config: VisionModelProviderConfig) -> tuple[VisionModelAdapter, str | None]:
    try:
        if config.provider == "mock":
            return MockVisionModelAdapter(model=config.model, prompt=config.prompt), None
        if config.provider == "openai-compatible":
            return OpenAICompatibleVisionModelAdapter(
                model=config.model,
                request_timeout_seconds=config.request_timeout_seconds,
                max_retries=config.max_retries,
                prompt=config.prompt,
            ), None
        if config.provider == "dashscope-compatible":
            return DashScopeCompatibleVisionModelAdapter(
                model=config.model,
                request_timeout_seconds=config.request_timeout_seconds,
                max_retries=config.max_retries,
                prompt=config.prompt,
            ), None
        raise ProviderUnavailable(f"unsupported vision model provider: {config.provider}")
    except ProviderUnavailable as exc:
        if not config.allow_mock_fallback:
            raise
        return MockVisionModelAdapter(model="mock-vision", prompt=config.prompt), str(exc)


def _extract_recognition_event(result) -> TranscriptEvent | None:
    """从 DashScope RecognitionResult 中提取结构化实时 ASR 事件。

    主要逻辑：Paraformer realtime 的 `result-generated` 可能先返回
    `sentence_begin=true` 且 `text=""` 的边界事件，后续再返回文本增量和
    `sentence_end=true` 的 final。这里保留完整 sentence 字段，避免 Vision 链路
    只能看到 text/final 而丢失 provider VAD 信号。
    参数：`result` 为 DashScope SDK 回调结果。
    返回值：可供 VisionRealtimeAgentCore 消费的 TranscriptEvent；无有效 sentence 时返回 None。
    异常情况：SDK 字段读取失败时返回 None。
    """

    getter = getattr(result, "get_sentence", None)
    if not callable(getter):
        return None
    try:
        sentence = getter()
    except Exception:
        return None
    if not isinstance(sentence, dict):
        return None
    text = str(sentence.get("text") or "")
    sentence_begin = bool(sentence.get("sentence_begin") is True)
    sentence_end = bool(sentence.get("sentence_end") is True or sentence.get("end_time") is not None)
    if not text and not sentence_begin and not sentence_end:
        return None
    return TranscriptEvent(
        text=text,
        final=sentence_end,
        sentence_id=_int_or_none(sentence.get("sentence_id")),
        sentence_begin=sentence_begin,
        sentence_end=sentence_end,
        begin_time_ms=_int_or_none(sentence.get("begin_time")),
        end_time_ms=_int_or_none(sentence.get("end_time")),
        words=sentence.get("words") if isinstance(sentence.get("words"), list) else None,
        raw=dict(sentence),
    )


def _extract_recognition_sentence(result) -> tuple[str, bool]:
    """兼容旧测试和旧调用点的 DashScope sentence 抽取函数。"""

    event = _extract_recognition_event(result)
    if event is None:
        return "", False
    return event.text, event.final


def _int_or_none(value) -> int | None:
    """把 provider 返回的时间戳或编号转换为 int。"""

    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
