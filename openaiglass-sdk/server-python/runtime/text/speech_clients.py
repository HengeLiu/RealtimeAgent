"""文本语音模式使用的 ASR/TTS/兼容语音模型客户端。"""

from __future__ import annotations

import base64
import json
import threading
import time
from typing import Any, Callable, Iterable

from infra.config import ServerSettings
from infra.errors import AppError, ErrorCode, build_error
from infra.logging import LogContext, get_logger, log_debug, log_info
from runtime.model_payloads import build_audio_data_url, extract_message_text, extract_text_delta, read_attr_or_key
from runtime.voice_constants import MODEL_OUTPUT_SAMPLE_RATE_HZ, SERVER_SAMPLE_RATE_HZ, SERVER_SAMPLE_WIDTH_BYTES
from runtime.voice_models import ModelChunk


class StreamingTtsSession:
    """流式 TTS 会话抽象接口。"""

    def push_text(self, text_delta: str) -> None:
        """推送一段新的文本增量。"""

        raise NotImplementedError

    def finish(self) -> None:
        """通知 TTS 当前文本已经全部推送完成。"""

        raise NotImplementedError


class BufferedStreamingTtsSession(StreamingTtsSession):
    """退化版流式 TTS 会话。

    主要功能：
    1. 在不支持真正增量合成时，先缓存所有文本。
    2. 在 `finish()` 时一次性调用旧的全文 TTS 生成音频。
    """

    def __init__(
        self,
        *,
        client: VoiceModelClient,
        settings: ServerSettings,
        on_chunk: Callable[[ModelChunk], None],
    ) -> None:
        self._client = client
        self._settings = settings
        self._on_chunk = on_chunk
        self._parts: list[str] = []

    def push_text(self, text_delta: str) -> None:
        if text_delta:
            self._parts.append(text_delta)

    def finish(self) -> None:
        text = "".join(self._parts).strip()
        if not text:
            text = "收到。"
        for chunk in self._client.stream_tts(settings=self._settings, text=text):
            self._on_chunk(chunk)


def _normalize_tts_event_payload(event: Any) -> dict[str, Any]:
    """归一化 TTS 事件对象。

    主要逻辑：
    1. 兼容字典、JSON 字符串和 SDK 对象三种输入。
    2. 抽取 `header` 与 `payload` 两层结构，便于统一解析。

    参数：
    1. `event`：DashScope 回调给出的事件对象。

    返回值：
    1. 统一后的事件字典；无法识别时返回空字典。
    """

    if event is None:
        return {}
    if isinstance(event, dict):
        return event
    if isinstance(event, str):
        try:
            parsed = json.loads(event)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    for method_name in ("to_dict", "model_dump", "dict"):
        method = getattr(event, method_name, None)
        if callable(method):
            try:
                payload = method()
            except TypeError:
                continue
            if isinstance(payload, dict):
                return payload

    header = _normalize_tts_event_payload(getattr(event, "header", None))
    payload = _normalize_tts_event_payload(getattr(event, "payload", None))
    if header or payload:
        return {"header": header, "payload": payload}
    return {}


def _extract_tts_event_summary(event: Any) -> dict[str, Any] | None:
    """提取需要保留的 TTS 关键事件摘要。

    主要逻辑：
    1. 只保留句子结束和任务完成两类节点。
    2. 提取任务编号、句子序号、句子文本与字符数，便于排查问题。

    参数：
    1. `event`：DashScope 回调给出的事件对象。

    返回值：
    1. 关键事件摘要；若当前事件无需记录则返回 `None`。
    """

    payload = _normalize_tts_event_payload(event)
    if not payload:
        return None

    header = payload.get("header")
    payload_body = payload.get("payload")
    if not isinstance(header, dict) or not isinstance(payload_body, dict):
        return None

    task_id = header.get("task_id")
    if header.get("event") == "task-finished":
        return {"kind": "task-finished", "task_id": task_id}

    output = payload_body.get("output")
    if not isinstance(output, dict):
        return None
    if output.get("type") != "sentence-end":
        return None

    sentence = output.get("sentence")
    usage = output.get("usage")
    if not isinstance(sentence, dict):
        sentence = {}
    if not isinstance(usage, dict):
        usage = {}

    return {
        "kind": "sentence-end",
        "task_id": task_id,
        "sentence_index": sentence.get("index"),
        "text": (output.get("original_text") or "").strip(),
        "characters": usage.get("characters"),
    }


class DashscopeCosyVoiceTtsSession(StreamingTtsSession):
    """基于百炼 CosyVoice WebSocket 的真正流式 TTS 会话。"""

    def __init__(
        self,
        *,
        settings: ServerSettings,
        on_chunk: Callable[[ModelChunk], None],
    ) -> None:
        try:
            import dashscope
            from dashscope.audio.tts_v2 import AudioFormat, ResultCallback, SpeechSynthesizer
        except ImportError as exc:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "缺少 dashscope 依赖，无法启用 CosyVoice 流式 TTS",
                details={"hint": "请执行 uv sync 安装 dashscope"},
            ) from exc

        self._settings = settings
        self._on_chunk = on_chunk
        self._error_message: str | None = None
        self._completed = threading.Event()
        self._closed = threading.Event()
        self._logger = get_logger("server.voice")
        self._task_id: str | None = None
        self._metrics_lock = threading.Lock()
        self._created_at_ms = self._now_ms()
        self._opened_at_ms: int | None = None
        self._first_text_push_started_at_ms: int | None = None
        self._first_text_push_returned_at_ms: int | None = None
        self._first_data_at_ms: int | None = None
        self._text_chars_pushed = 0
        self._text_push_count = 0
        self._text_chars_before_first_data: int | None = None
        self._text_push_count_before_first_data: int | None = None
        self._stream_start_lock = threading.Lock()
        self._prewarm_done = threading.Event()
        self._prewarm_started_at_ms: int | None = None
        self._prewarm_completed_at_ms: int | None = None
        self._prewarm_error: str | None = None
        self._prewarmed_stream = False

        dashscope.api_key = settings.dashscope_api_key
        dashscope.base_websocket_api_url = settings.tts_websocket_api_url

        completed_event = self._completed
        closed_event = self._closed
        error_box = self
        chunk_sink = self._on_chunk
        sample_rate_hz = settings.tts_sample_rate_hz

        class _Callback(ResultCallback):
            """CosyVoice 回调桥接器。"""

            def on_open(self):  # pragma: no cover - 真实联调路径
                error_box._mark_opened()
                return None

            def on_complete(self):  # pragma: no cover - 真实联调路径
                completed_event.set()

            def on_error(self, message: str):  # pragma: no cover - 真实联调路径
                error_box._error_message = message
                completed_event.set()

            def on_close(self):  # pragma: no cover - 真实联调路径
                closed_event.set()

            def on_event(self, message):  # pragma: no cover - 真实联调路径
                error_box._handle_event(message)

            def on_data(self, data: bytes) -> None:  # pragma: no cover - 真实联调路径
                if data:
                    error_box._mark_first_data(data)
                    chunk_sink(ModelChunk(audio_pcm_bytes=data, sample_rate_hz=sample_rate_hz))

        self._synthesizer = SpeechSynthesizer(
            model=settings.tts_model_name,
            voice=settings.tts_voice,
            format=AudioFormat.PCM_22050HZ_MONO_16BIT,
            callback=_Callback(),
        )
        threading.Thread(
            target=self._prewarm_stream,
            name="dashscope-tts-prewarm",
            daemon=True,
        ).start()

    def push_text(self, text_delta: str) -> None:
        if text_delta:
            self._wait_for_prewarm()
            started_at_ms = self._mark_text_push_started(text_delta)
            with self._stream_start_lock:
                self._synthesizer.streaming_call(text_delta)
            self._mark_text_push_returned(started_at_ms, text_delta)

    def finish(self) -> None:
        log_debug(
            self._logger,
            (
                "TTS 文本推送完成，等待音频收尾: "
                f"task_id={self._task_id or '<pending>'} "
                f"model={self._settings.tts_model_name} voice={self._settings.tts_voice}"
            ),
        )
        self._synthesizer.streaming_complete()
        completed = self._completed.wait(timeout=max(5.0, self._settings.voice_model_timeout_ms / 1000))
        if not completed:
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                "CosyVoice 流式 TTS 等待完成超时",
            )
        if self._error_message:
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                "CosyVoice 流式 TTS 调用失败",
                details={"reason": self._error_message},
            )

    def _handle_event(self, event: Any) -> None:
        """处理 DashScope 回调事件并输出精简日志。"""

        summary = _extract_tts_event_summary(event)
        if summary is None:
            return

        task_id = summary.get("task_id")
        if isinstance(task_id, str) and task_id.strip():
            self._task_id = task_id

        if summary["kind"] == "sentence-end":
            log_debug(
                self._logger,
                (
                    "TTS 句子完成: "
                    f"task_id={self._task_id or '<unknown>'} "
                    f"sentence_index={summary.get('sentence_index')} "
                    f"characters={summary.get('characters')} "
                    f"text={summary.get('text')}"
                ),
            )
            return

        if summary["kind"] == "task-finished":
            log_debug(
                self._logger,
                f"TTS 任务完成: task_id={self._task_id or '<unknown>'}",
            )

    def _prewarm_stream(self) -> None:
        """在后台提前打开 CosyVoice 流式任务。

        主要逻辑：
        1. DashScope `SpeechSynthesizer` 默认在首次 `streaming_call(...)` 时才连接
           WebSocket 并发送 run-task 请求。
        2. 这里复用 SDK 内部的 start-stream 能力，把连接和 run-task 握手前移到
           Agent 请求等待期间。
        3. 如果预热失败，不中断主链路；首次文本到来时仍按原逻辑触发建连，失败会由
           上层重建 TTS 会话。

        参数：无。
        返回值：无。
        异常情况：所有异常都会记录到调试日志并退化为首次文本触发。
        """

        started_at_ms = self._now_ms()
        with self._metrics_lock:
            self._prewarm_started_at_ms = started_at_ms

        start_stream = getattr(self._synthesizer, "_SpeechSynthesizer__start_stream", None)
        if not callable(start_stream):
            self._prewarm_error = "dashscope_start_stream_not_available"
            self._prewarm_done.set()
            log_debug(
                self._logger,
                "TTS 预热流启动跳过: 当前 dashscope SDK 未暴露 start_stream 内部能力",
                LogContext(session_id="tts", device_id="server", message_id="streaming_tts"),
            )
            return

        try:
            with self._stream_start_lock:
                start_stream()
                # start_stream 已经发送 run-task。把首次标记改为 false，避免后续
                # streaming_call 再次启动同一个任务。
                setattr(self._synthesizer, "_is_first", False)
            completed_at_ms = self._now_ms()
            with self._metrics_lock:
                self._prewarm_completed_at_ms = completed_at_ms
                self._prewarmed_stream = True
                opened_at_ms = self._opened_at_ms
            log_info(
                self._logger,
                (
                    "TTS 预热流已启动 "
                    f"prewarm_stream_cost_ms={self._latency_ms(started_at_ms, completed_at_ms)} "
                    f"session_create_to_prewarm_stream_ms={self._latency_ms(self._created_at_ms, completed_at_ms)} "
                    f"session_create_to_open_ms={self._latency_ms(self._created_at_ms, opened_at_ms)} "
                    f"model={self._settings.tts_model_name} voice={self._settings.tts_voice}"
                ),
                LogContext(session_id="tts", device_id="server", message_id="streaming_tts"),
            )
        except Exception as exc:  # pragma: no cover - 真实联调容错路径
            self._prewarm_error = repr(exc)
            log_debug(
                self._logger,
                f"TTS 预热流启动失败，回退到首次文本触发: reason={exc!r}",
                LogContext(session_id="tts", device_id="server", message_id="streaming_tts"),
            )
        finally:
            self._prewarm_done.set()

    def _wait_for_prewarm(self) -> None:
        """首个文本推送前等待后台预热结束，避免和预热线程重复启动任务。"""

        if self._prewarm_done.is_set():
            return
        wait_started_at_ms = self._now_ms()
        self._prewarm_done.wait(timeout=2.0)
        waited_ms = self._latency_ms(wait_started_at_ms, self._now_ms())
        if self._prewarm_done.is_set():
            log_debug(
                self._logger,
                (
                    "TTS 首次文本等待预热完成 "
                    f"wait_ms={waited_ms} prewarmed={self._prewarmed_stream} "
                    f"error={self._prewarm_error}"
                ),
                LogContext(session_id="tts", device_id="server", message_id="streaming_tts"),
            )
            return
        log_debug(
            self._logger,
            f"TTS 预热仍未完成，首次文本将等待同一启动锁: wait_ms={waited_ms}",
            LogContext(session_id="tts", device_id="server", message_id="streaming_tts"),
        )

    @staticmethod
    def _now_ms() -> int:
        """返回当前毫秒时间戳。"""

        return int(time.time() * 1000)

    @staticmethod
    def _latency_ms(start: int | None, end: int | None) -> int | None:
        """计算两个毫秒时间戳之间的非负耗时。"""

        if start is None or end is None:
            return None
        return max(end - start, 0)

    def _mark_opened(self) -> None:
        """记录 TTS WebSocket 打开时间。"""

        with self._metrics_lock:
            if self._opened_at_ms is not None:
                return
            self._opened_at_ms = self._now_ms()
            opened_at_ms = self._opened_at_ms
            created_at_ms = self._created_at_ms
        log_debug(
            self._logger,
            (
                "TTS WebSocket 已打开 "
                f"session_create_to_open_ms={self._latency_ms(created_at_ms, opened_at_ms)} "
                f"model={self._settings.tts_model_name} voice={self._settings.tts_voice}"
            ),
            LogContext(session_id="tts", device_id="server", message_id="streaming_tts"),
        )

    def _mark_text_push_started(self, text_delta: str) -> int:
        """记录一次文本增量推送开始，并返回开始时间。"""

        started_at_ms = self._now_ms()
        with self._metrics_lock:
            if self._first_text_push_started_at_ms is None:
                self._first_text_push_started_at_ms = started_at_ms
            self._text_chars_pushed += len(text_delta)
            self._text_push_count += 1
        return started_at_ms

    def _mark_text_push_returned(self, started_at_ms: int, text_delta: str) -> None:
        """记录首次 `streaming_call(...)` 返回耗时。"""

        returned_at_ms = self._now_ms()
        should_log = False
        with self._metrics_lock:
            if self._first_text_push_returned_at_ms is None:
                self._first_text_push_returned_at_ms = returned_at_ms
                should_log = True
                created_at_ms = self._created_at_ms
                opened_at_ms = self._opened_at_ms
                first_started_at_ms = self._first_text_push_started_at_ms
                text_chars_pushed = self._text_chars_pushed
                text_push_count = self._text_push_count
            else:
                created_at_ms = None
                opened_at_ms = None
                first_started_at_ms = None
                text_chars_pushed = 0
                text_push_count = 0
        if not should_log:
            return
        log_info(
            self._logger,
            (
                "TTS 首次文本已推送 "
                f"first_streaming_call_cost_ms={self._latency_ms(started_at_ms, returned_at_ms)} "
                f"session_create_to_first_push_ms={self._latency_ms(created_at_ms, first_started_at_ms)} "
                f"websocket_open_to_first_push_ms={self._latency_ms(opened_at_ms, first_started_at_ms)} "
                f"text_chars={len(text_delta)} total_text_chars={text_chars_pushed} push_count={text_push_count}"
            ),
            LogContext(session_id="tts", device_id="server", message_id="streaming_tts"),
        )

    def _mark_first_data(self, data: bytes) -> None:
        """记录 TTS 服务返回首段音频的接口级耗时。"""

        first_data_at_ms = self._now_ms()
        should_log = False
        with self._metrics_lock:
            if self._first_data_at_ms is None:
                self._first_data_at_ms = first_data_at_ms
                self._text_chars_before_first_data = self._text_chars_pushed
                self._text_push_count_before_first_data = self._text_push_count
                should_log = True
                created_at_ms = self._created_at_ms
                opened_at_ms = self._opened_at_ms
                first_push_started_at_ms = self._first_text_push_started_at_ms
                first_push_returned_at_ms = self._first_text_push_returned_at_ms
                text_chars = self._text_chars_before_first_data
                push_count = self._text_push_count_before_first_data
            else:
                created_at_ms = None
                opened_at_ms = None
                first_push_started_at_ms = None
                first_push_returned_at_ms = None
                text_chars = None
                push_count = None
        if not should_log:
            return
        log_info(
            self._logger,
            (
                "TTS 服务返回首段音频 "
                f"tts_first_audio_latency_ms={self._latency_ms(first_push_started_at_ms, first_data_at_ms)} "
                f"tts_first_audio_after_call_return_ms={self._latency_ms(first_push_returned_at_ms, first_data_at_ms)} "
                f"session_create_to_first_audio_ms={self._latency_ms(created_at_ms, first_data_at_ms)} "
                f"websocket_open_to_first_audio_ms={self._latency_ms(opened_at_ms, first_data_at_ms)} "
                f"text_chars_before_first_audio={text_chars} text_push_count_before_first_audio={push_count} "
                f"bytes={len(data)}"
            ),
            LogContext(session_id="tts", device_id="server", message_id="streaming_tts"),
        )


class VoiceModelClient:
    """语音模型客户端接口。"""

    def stream_reply(self, *, settings: ServerSettings, messages: list[dict[str, Any]]) -> Iterable[ModelChunk]:
        raise NotImplementedError

    def stream_tts(self, *, settings: ServerSettings, text: str) -> Iterable[ModelChunk]:
        """把文本转换为可播放语音流。

        主要逻辑：
        1. 当前默认通过同一语音模型执行“文本转音频”。
        2. 使用严格的“原样朗读”提示词，尽量避免改写文本。

        参数：
        1. `settings`：服务端配置。
        2. `text`：待播报文本。

        返回值：
        1. 流式音频分片。
        """

        prompt = text.strip() or "收到。"
        return self.stream_reply(
            settings=settings,
            messages=[
                {
                    "role": "system",
                    "content": "你现在只负责把用户提供的文本原样朗读成语音，不要补充解释，不要改写。",
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
        )

    def create_streaming_tts_session(
        self,
        *,
        settings: ServerSettings,
        on_chunk: Callable[[ModelChunk], None],
    ) -> StreamingTtsSession:
        """创建一个支持增量文本输入的 TTS 会话。

        主要逻辑：
        1. 默认返回退化版实现。
        2. 若子类支持真正的增量语音合成，可以覆盖该方法。
        """

        return BufferedStreamingTtsSession(
            client=self,
            settings=settings,
            on_chunk=on_chunk,
        )


class SpeechRecognitionClient:
    """语音转写客户端接口。

    主要功能：
    1. 接收一段完整用户语音。
    2. 返回该轮语音的转写文本。
    """

    def transcribe(self, *, settings: ServerSettings, input_wav: bytes) -> str:
        """把一段完整 WAV 音频转成文本。"""

        raise NotImplementedError

    def start_streaming_session(
        self,
        *,
        settings: ServerSettings,
        session_id: str,
        device_id: str,
        segment_id: str,
        stream_id: str,
        sample_rate_hz: int,
        channels: int,
        codec: str,
    ) -> "StreamingSpeechRecognitionSession | None":
        """启动实时 ASR 会话。

        主要逻辑：
        1. 默认客户端不支持实时 ASR，返回 `None`。
        2. 支持实时能力的子类应返回可持续接收 PCM 分片的会话对象。

        返回值：
        1. `StreamingSpeechRecognitionSession` 或 `None`。
        """

        return None


def _extract_recognition_sentence(result: Any) -> tuple[str, bool]:
    """从 DashScope RecognitionResult 中提取文本和句尾标记。

    主要逻辑：
    1. 读取 `result.get_sentence()` 返回的句子字典。
    2. 提取当前识别文本。
    3. 按官方 SDK 语义，用 `end_time` 是否存在判断当前句子是否结束。

    参数：
    1. `result`：DashScope 实时 ASR 回调传入的 RecognitionResult。

    返回值：
    1. `(text, is_sentence_end)`，文本为空表示当前事件没有可用识别结果。
    """

    sentence_getter = getattr(result, "get_sentence", None)
    if not callable(sentence_getter):
        return "", False
    try:
        sentence = sentence_getter()
    except Exception:
        return "", False
    if not isinstance(sentence, dict):
        return "", False
    text = sentence.get("text")
    if text is None:
        return "", False
    return str(text), sentence.get("end_time") is not None


class StreamingSpeechRecognitionSession:
    """实时 ASR 会话抽象。

    主要功能：
    1. 在眼镜上传音频帧时同步接收 PCM 分片。
    2. 在用户说完后尽快返回实时 ASR 已经得到的最终文本。
    """

    def append_audio(self, pcm_bytes: bytes) -> None:
        """追加一段 PCM 音频。"""

        raise NotImplementedError

    def finish(self) -> str:
        """结束音频输入并返回最终转写文本。"""

        raise NotImplementedError

    def metrics(self) -> dict[str, int | None]:
        """返回实时 ASR 计时指标。

        返回值：
        1. 指标名到毫秒值的字典；不支持的实现可以返回空字典。
        """

        return {}


class DashscopeVoiceModelClient(VoiceModelClient):
    """百炼兼容 Chat Completions 语音客户端。

    主要功能：
    1. 使用 OpenAI SDK 的兼容模式连接百炼服务。
    2. 发起流式多模态对话请求。
    3. 解析增量文本与增量音频分片。

    主要属性：
    1. `_sdk_client`：可选的 SDK 客户端注入点，便于测试时替换。
    """

    def __init__(self, sdk_client: Any | None = None) -> None:
        """初始化百炼模型客户端。

        参数：
        1. `sdk_client`：可选的 OpenAI SDK 客户端；测试时可注入假对象。
        """

        self._sdk_client = sdk_client

    def create_streaming_tts_session(
        self,
        *,
        settings: ServerSettings,
        on_chunk: Callable[[ModelChunk], None],
    ) -> StreamingTtsSession:
        """创建 DashScope CosyVoice 流式 TTS 会话。

        主要逻辑：
        1. 优先尝试使用官方 `dashscope.audio.tts_v2` WebSocket 能力。
        2. 若本地缺少依赖或初始化失败，则回退到兼容旧逻辑的全文 TTS。
        """

        try:
            session = DashscopeCosyVoiceTtsSession(
                settings=settings,
                on_chunk=on_chunk,
            )
            log_debug(
                get_logger("server.voice"),
                (
                    "CosyVoice 流式 TTS 初始化成功: "
                    f"model={settings.tts_model_name} voice={settings.tts_voice} "
                    f"sample_rate_hz={settings.tts_sample_rate_hz}"
                ),
                LogContext(session_id="tts", device_id="server", message_id="streaming_tts"),
            )
            return session
        except Exception as exc:  # pragma: no cover - 依赖缺失时走降级
            log_debug(
                get_logger("server.voice"),
                f"CosyVoice 流式 TTS 初始化失败，回退全文 TTS: reason={exc!r}",
                LogContext(session_id="tts", device_id="server", message_id="streaming_tts"),
            )
            return super().create_streaming_tts_session(settings=settings, on_chunk=on_chunk)

    def stream_reply(self, *, settings: ServerSettings, messages: list[dict[str, Any]]) -> Iterable[ModelChunk]:
        """调用百炼兼容接口并返回流式回复。

        主要逻辑：
        1. 校验 `DASHSCOPE_API_KEY` 是否存在。
        2. 使用 OpenAI SDK 创建 `chat.completions.create(..., stream=True)` 请求。
        3. 逐条解析 SDK 返回的分片，抽取文本增量与音频增量。

        参数：
        1. `settings`：服务端配置。
        2. `messages`：已组装完成的模型消息列表。

        返回值：
        1. 逐条产出 `ModelChunk`。

        异常情况：
        1. 缺少 API Key 时抛出 `AppError(INVALID_CONFIG)`。
        2. SDK 调用失败时抛出 `AppError(INTERNAL_ERROR)`，并附带状态码与响应体。
        """

        if not settings.dashscope_api_key.strip():
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "缺少 DASHSCOPE_API_KEY，无法执行真实语音对话",
            )

        client = self._sdk_client or self._create_sdk_client(settings)

        try:
            completion = client.chat.completions.create(
                model=settings.voice_model_name,
                messages=messages,
                modalities=["text", "audio"],
                audio={
                    "voice": settings.voice_model_voice,
                    "format": "wav",
                },
                stream=True,
                stream_options={"include_usage": True},
                timeout=settings.voice_model_timeout_ms / 1000,
            )
            for chunk in completion:
                parsed = self._parse_chunk(chunk)
                if parsed is not None:
                    yield parsed
        except Exception as exc:
            response_body = self._extract_sdk_error_body(exc)
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                "百炼模型接口调用失败",
                details={
                    "status": getattr(exc, "status_code", None),
                    "reason": str(exc),
                    "body": response_body,
                },
            ) from exc

    @staticmethod
    def _create_sdk_client(settings: ServerSettings) -> Any:
        """创建 OpenAI SDK 客户端。

        参数：
        1. `settings`：服务端配置。

        返回值：
        1. OpenAI SDK 客户端实例。

        异常情况：
        1. 未安装 `openai` 依赖时抛出 `AppError(INVALID_CONFIG)`。
        """

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "缺少 openai 依赖，无法通过 SDK 调用百炼兼容接口",
                details={"hint": "请执行 uv sync 或安装 openai 依赖"},
            ) from exc

        return OpenAI(
            api_key=settings.dashscope_api_key,
            base_url=settings.voice_model_base_url.rstrip("/"),
        )

    @staticmethod
    def _parse_chunk(event: Any) -> ModelChunk | None:
        """解析 SDK 流式分片。

        参数：
        1. `event`：OpenAI SDK 返回的单条流式对象。

        返回值：
        1. 有效分片时返回 `ModelChunk`，否则返回 `None`。
        """

        choices = getattr(event, "choices", None)
        if not isinstance(choices, list) or not choices:
            return None
        first_choice = choices[0]
        delta = getattr(first_choice, "delta", None)
        text_delta = extract_text_delta(getattr(delta, "content", None))
        audio = getattr(delta, "audio", None)
        audio_pcm = b""
        sample_rate = MODEL_OUTPUT_SAMPLE_RATE_HZ
        audio_data = read_attr_or_key(audio, "data")
        if isinstance(audio_data, str) and audio_data:
            audio_pcm = base64.b64decode(audio_data)
        sample_rate_raw = read_attr_or_key(audio, "sample_rate")
        if isinstance(sample_rate_raw, int) and sample_rate_raw > 0:
            sample_rate = sample_rate_raw
        if not text_delta and not audio_pcm:
            return None
        return ModelChunk(text_delta=text_delta, audio_pcm_bytes=audio_pcm, sample_rate_hz=sample_rate)

    @staticmethod
    def _extract_sdk_error_body(exc: Exception) -> str:
        """从 SDK 异常对象里尽量提取响应体文本。

        参数：
        1. `exc`：SDK 抛出的异常对象。

        返回值：
        1. 可读的响应体文本；若无法提取则返回空字符串。
        """

        body = getattr(exc, "body", None)
        if body is not None:
            return str(body)
        response = getattr(exc, "response", None)
        if response is None:
            return ""
        text = getattr(response, "text", None)
        if callable(text):
            try:
                return str(text())
            except Exception:
                return ""
        if text is not None:
            return str(text)
        return ""


class DashscopeSpeechRecognitionClient(SpeechRecognitionClient):
    """百炼 ASR 客户端。

    主要功能：
    1. 使用 OpenAI SDK 兼容模式调用百炼 ASR 模型。
    2. 把完整用户音频转成纯文本。
    """

    def __init__(self, sdk_client: Any | None = None) -> None:
        """初始化 ASR 客户端。

        参数：
        1. `sdk_client`：可选的 OpenAI SDK 客户端；测试时可注入假对象。
        """

        self._sdk_client = sdk_client

    def start_streaming_session(
        self,
        *,
        settings: ServerSettings,
        session_id: str,
        device_id: str,
        segment_id: str,
        stream_id: str,
        sample_rate_hz: int,
        channels: int,
        codec: str,
    ) -> StreamingSpeechRecognitionSession | None:
        """启动百炼实时 ASR 会话。

        主要逻辑：
        1. 仅在 `VOICE_ASR_MODE=realtime`、16k 单声道 PCM16 输入下启用。
        2. 返回后台 WebSocket 会话，音频帧会边到达边转发给 ASR 服务。
        3. 初始化失败不阻塞语音主链路，由旧批量 ASR 兜底。
        """

        if settings.voice_asr_mode != "realtime":
            return None
        if channels != 1 or codec.lower() not in {"pcm16", "pcm16le"}:
            return None
        if sample_rate_hz != SERVER_SAMPLE_RATE_HZ:
            return None
        if not settings.dashscope_api_key.strip():
            return None
        try:
            return DashscopeRealtimeSpeechRecognitionSession(
                settings=settings,
                session_id=session_id,
                device_id=device_id,
                segment_id=segment_id,
                stream_id=stream_id,
                sample_rate_hz=sample_rate_hz,
            )
        except Exception as exc:
            log_debug(
                get_logger("server.voice"),
                (
                    "实时 ASR 会话启动失败，回退批量 ASR: "
                    f"segment_id={segment_id} input_stream_id={stream_id} error={exc!r}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )
            return None

    def transcribe(self, *, settings: ServerSettings, input_wav: bytes) -> str:
        """调用百炼 ASR 把语音转写成文本。

        主要逻辑：
        1. 使用独立 ASR 模型 `qwen3-asr-flash`。
        2. 通过 OpenAI SDK 兼容接口提交单轮音频。
        3. 从返回结果中提取转写文本。

        参数：
        1. `settings`：服务端配置。
        2. `input_wav`：完整 WAV 音频。

        返回值：
        1. 转写后的文本；若为空则返回空字符串。

        异常情况：
        1. SDK 调用失败时抛出 `AppError(INTERNAL_ERROR)`。
        """

        if not settings.dashscope_api_key.strip():
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "缺少 DASHSCOPE_API_KEY，无法执行语音转写",
            )

        client = self._sdk_client or DashscopeVoiceModelClient._create_sdk_client(settings)
        try:
            completion = client.chat.completions.create(
                model=getattr(settings, "voice_asr_model_name", "qwen3-asr-flash"),
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_audio",
                                "input_audio": {
                                    "data": build_audio_data_url(input_wav),
                                },
                            },
                        ],
                    }
                ],
                stream=False,
                extra_body={
                    "asr_options": {
                        "enable_itn": False,
                    }
                },
                timeout=settings.voice_model_timeout_ms / 1000,
            )
        except Exception as exc:
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                "百炼 ASR 接口调用失败",
                details={
                    "status": getattr(exc, "status_code", None),
                    "reason": str(exc),
                    "body": DashscopeVoiceModelClient._extract_sdk_error_body(exc),
                },
            ) from exc

        text = extract_message_text(completion)
        return text.strip()


class DashscopeRealtimeSpeechRecognitionSession(StreamingSpeechRecognitionSession):
    """基于百炼 Recognition SDK 的 Fun-ASR 实时转写会话。

    主要功能：
    1. 按官方实时 ASR SDK，在会话启动后持续调用 `send_audio_frame(...)`。
    2. 通过 `RecognitionCallback.on_event(...)` 接收中间识别文本和句尾文本。
    3. 在 `finish()` 中调用 `stop()`，等待服务端返回完整识别结果。

    主要属性：
    1. `_first_audio_chunk_at_ms`：服务端收到并送入 ASR 的首个音频 chunk 时间。
    2. `_first_partial_at_ms`：ASR 服务返回第一段文本的时间。
    3. `_completed_at_ms`：ASR 服务完成本轮实时识别的时间。
    """

    def __init__(
        self,
        *,
        settings: ServerSettings,
        session_id: str,
        device_id: str,
        segment_id: str,
        stream_id: str,
        sample_rate_hz: int,
    ) -> None:
        self._settings = settings
        self._session_id = session_id
        self._device_id = device_id
        self._segment_id = segment_id
        self._stream_id = stream_id
        self._sample_rate_hz = sample_rate_hz
        self._created_at_ms = int(time.time() * 1000)
        self._done_event = threading.Event()
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self._error: BaseException | None = None
        self._final_sentences: list[str] = []
        self._final_text = ""
        self._latest_partial_text = ""
        self._first_audio_chunk_at_ms: int | None = None
        self._first_audio_send_returned_at_ms: int | None = None
        self._first_partial_at_ms: int | None = None
        self._first_sentence_end_at_ms: int | None = None
        self._final_text_at_ms: int | None = None
        self._completed_at_ms: int | None = None
        self._start_called_at_ms: int | None = None
        self._open_at_ms: int | None = None
        self._stop_requested_at_ms: int | None = None
        self._audio_frame_count = 0
        self._audio_bytes_sent = 0
        self._audio_bytes_before_first_partial: int | None = None
        self._dashscope_first_package_delay_ms: int | None = None
        self._dashscope_last_package_delay_ms: int | None = None
        self._logger = get_logger("server.voice")
        self._recognition = self._create_recognition()
        self._start_called_at_ms = int(time.time() * 1000)
        self._recognition.start()

    def _create_recognition(self) -> Any:
        """创建官方 DashScope Recognition 实时 ASR 会话。

        主要逻辑：
        1. 设置 DashScope API Key 和 WebSocket 地址。
        2. 通过 `RecognitionCallback` 接收实时识别事件。
        3. 创建 `Recognition` 对象，输入格式固定为裸 PCM16 音频。

        返回值：
        1. 已配置但尚未 start 的 Recognition 实例。

        异常情况：
        1. dashscope 依赖缺失或 Recognition 初始化失败时向上抛出，由上层回退批量 ASR。
        """

        import dashscope
        from dashscope.audio.asr import Recognition, RecognitionCallback

        session = self
        dashscope.api_key = self._settings.dashscope_api_key
        dashscope.base_websocket_api_url = self._settings.tts_websocket_api_url

        class _Callback(RecognitionCallback):
            """DashScope 实时 ASR 回调桥。"""

            def on_open(self) -> None:  # pragma: no cover - 真实联调路径
                session._open_at_ms = int(time.time() * 1000)
                log_debug(
                    session._logger,
                    (
                        "实时 ASR 连接已建立 "
                        f"model={session._settings.voice_asr_realtime_model_name} "
                        f"segment_id={session._segment_id} input_stream_id={session._stream_id} "
                        f"recognition_open_latency_ms="
                        f"{session._latency_ms(session._created_at_ms, session._open_at_ms)}"
                    ),
                    LogContext(device_id=session._device_id, session_id=session._session_id),
                )

            def on_close(self) -> None:  # pragma: no cover - 真实联调路径
                return None

            def on_complete(self) -> None:
                session._handle_recognition_complete()

            def on_error(self, result: Any) -> None:
                session._handle_recognition_error(result)

            def on_event(self, result: Any) -> None:
                session._handle_recognition_event(result)

        return Recognition(
            model=self._settings.voice_asr_realtime_model_name,
            format="pcm",
            sample_rate=self._sample_rate_hz,
            semantic_punctuation_enabled=False,
            max_sentence_silence=self._settings.voice_asr_realtime_max_sentence_silence_ms,
            callback=_Callback(),
        )

    def append_audio(self, pcm_bytes: bytes) -> None:
        """追加实时 ASR 音频分片。"""

        if not pcm_bytes or self._closed.is_set():
            return
        now_ms = int(time.time() * 1000)
        is_first_audio = False
        with self._lock:
            if self._first_audio_chunk_at_ms is None:
                self._first_audio_chunk_at_ms = now_ms
                is_first_audio = True
            self._audio_frame_count += 1
            self._audio_bytes_sent += len(pcm_bytes)
        try:
            self._recognition.send_audio_frame(bytes(pcm_bytes))
            returned_at_ms = int(time.time() * 1000)
            if is_first_audio:
                self._first_audio_send_returned_at_ms = returned_at_ms
                log_debug(
                    self._logger,
                    (
                        "实时 ASR 首个音频分片已发送 "
                        f"segment_id={self._segment_id} input_stream_id={self._stream_id} "
                        f"bytes={len(pcm_bytes)} frame_count={self._audio_frame_count} "
                        f"session_start_to_first_audio_ms="
                        f"{self._latency_ms(self._created_at_ms, self._first_audio_chunk_at_ms)} "
                        f"send_audio_frame_cost_ms={self._latency_ms(now_ms, returned_at_ms)}"
                    ),
                    LogContext(device_id=self._device_id, session_id=self._session_id),
                )
        except Exception as exc:  # noqa: BLE001 - 真实 ASR SDK 可能抛出多类运行时异常
            self._error = build_error(
                ErrorCode.INTERNAL_ERROR,
                "实时 ASR 音频发送失败",
                details={"segment_id": self._segment_id, "stream_id": self._stream_id, "reason": str(exc)},
            )
            self._done_event.set()

    def finish(self) -> str:
        """结束实时 ASR 音频输入并返回最终文本。"""

        if not self._closed.is_set():
            self._closed.set()
            self._stop_requested_at_ms = int(time.time() * 1000)
            threading.Thread(
                target=self._stop_recognition,
                name=f"asr-stop-{self._device_id}-{self._segment_id}",
                daemon=True,
            ).start()
        completed = self._done_event.wait(timeout=max(self._settings.voice_asr_realtime_timeout_ms / 1000, 0.1))
        if not completed:
            raise build_error(
                ErrorCode.TIMEOUT,
                "实时 ASR 等待最终结果超时",
                retryable=True,
                details={"segment_id": self._segment_id, "stream_id": self._stream_id},
            )
        if self._error is not None:
            if isinstance(self._error, AppError):
                raise self._error
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                "实时 ASR 调用失败",
                details={"segment_id": self._segment_id, "reason": str(self._error)},
            )
        with self._lock:
            if self._completed_at_ms is None:
                self._completed_at_ms = int(time.time() * 1000)
            if not self._final_text and self._latest_partial_text:
                self._final_text = self._latest_partial_text
            return self._final_text.strip()

    def _stop_recognition(self) -> None:
        """在后台停止 DashScope Recognition 会话。

        主要逻辑：
        1. 调用官方 SDK 的 `stop()` 触发 ASR 收尾。
        2. `stop()` 内部会等待服务端完成，不能阻塞语音主链路线程。
        3. 如果停止失败，记录错误并唤醒 `finish()` 走批量 ASR 回退。
        """

        try:
            self._recognition.stop()
        except Exception as exc:  # noqa: BLE001 - stop 失败需要回退批量 ASR
            if self._error is None:
                self._error = build_error(
                    ErrorCode.INTERNAL_ERROR,
                    "实时 ASR 停止失败",
                    details={"segment_id": self._segment_id, "stream_id": self._stream_id, "reason": str(exc)},
                )
            self._done_event.set()

    def metrics(self) -> dict[str, int | None]:
        """返回实时 ASR 首文本和总耗时指标。"""

        total_finished_at_ms = self._completed_at_ms or self._final_text_at_ms
        return {
            "first_audio_chunk_at_ms": self._first_audio_chunk_at_ms,
            "first_asr_partial_latency_ms": self._latency_from_first_audio(self._first_partial_at_ms),
            "asr_total_latency_ms": self._latency_from_first_audio(total_finished_at_ms),
            "recognition_open_latency_ms": self._latency_ms(self._created_at_ms, self._open_at_ms),
            "session_start_to_first_audio_ms": self._latency_ms(self._created_at_ms, self._first_audio_chunk_at_ms),
            "first_audio_send_cost_ms": self._latency_ms(
                self._first_audio_chunk_at_ms,
                self._first_audio_send_returned_at_ms,
            ),
            "stop_to_complete_ms": self._latency_ms(self._stop_requested_at_ms, self._completed_at_ms),
            "audio_ms_before_first_partial": self._audio_duration_ms(self._audio_bytes_before_first_partial),
            "audio_frame_count": self._audio_frame_count,
            "audio_bytes_sent": self._audio_bytes_sent,
            "dashscope_first_package_delay_ms": self._dashscope_first_package_delay_ms,
            "dashscope_last_package_delay_ms": self._dashscope_last_package_delay_ms,
        }

    def _latency_from_first_audio(self, end_at_ms: int | None) -> int | None:
        """按首个音频分片时间计算耗时。"""

        if self._first_audio_chunk_at_ms is None or end_at_ms is None:
            return None
        return max(end_at_ms - self._first_audio_chunk_at_ms, 0)

    @staticmethod
    def _latency_ms(start_at_ms: int | None, end_at_ms: int | None) -> int | None:
        """计算两个毫秒时间戳之间的耗时。"""

        if start_at_ms is None or end_at_ms is None:
            return None
        return max(end_at_ms - start_at_ms, 0)

    def _audio_duration_ms(self, byte_count: int | None) -> int | None:
        """按 PCM16 单声道字节数估算已发送音频时长。"""

        if byte_count is None or self._sample_rate_hz <= 0:
            return None
        return int(byte_count * 1000 / (self._sample_rate_hz * SERVER_SAMPLE_WIDTH_BYTES))

    def _read_recognition_metric(self, method_name: str) -> int | None:
        """读取 DashScope SDK 自带的延迟指标。"""

        getter = getattr(self._recognition, method_name, None)
        if not callable(getter):
            return None
        try:
            value = getter()
        except Exception:
            return None
        if isinstance(value, (int, float)) and value >= 0:
            return int(value)
        return None

    def _handle_recognition_event(self, result: Any) -> None:
        """处理 DashScope Recognition 实时识别事件。"""

        text, is_sentence_end = _extract_recognition_sentence(result)
        if not text:
            return
        now_ms = int(time.time() * 1000)
        should_log_first_partial = False
        with self._lock:
            if self._first_partial_at_ms is None:
                self._first_partial_at_ms = now_ms
                self._audio_bytes_before_first_partial = self._audio_bytes_sent
                self._dashscope_first_package_delay_ms = self._read_recognition_metric("get_first_package_delay")
                should_log_first_partial = True
            if is_sentence_end:
                self._final_sentences.append(text)
                self._final_text = "".join(self._final_sentences)
                self._latest_partial_text = ""
                self._final_text_at_ms = now_ms
                if self._first_sentence_end_at_ms is None:
                    self._first_sentence_end_at_ms = now_ms
            else:
                self._latest_partial_text = text
        if should_log_first_partial:
            first_asr_partial_latency_ms = self._latency_from_first_audio(now_ms)
            audio_ms_before_first_partial = self._audio_duration_ms(self._audio_bytes_before_first_partial)
            log_debug(
                self._logger,
                (
                    "实时 ASR 返回首个文本 "
                    f"first_asr_partial_latency_ms={first_asr_partial_latency_ms} "
                    f"dashscope_first_package_delay_ms={self._dashscope_first_package_delay_ms} "
                    f"audio_ms_before_first_partial={audio_ms_before_first_partial} "
                    f"audio_bytes_before_first_partial={self._audio_bytes_before_first_partial} "
                    f"frame_count={self._audio_frame_count} "
                    f"segment_id={self._segment_id} input_stream_id={self._stream_id} "
                    f"is_sentence_end={is_sentence_end} text_preview={text[:24]!r}"
                ),
                LogContext(device_id=self._device_id, session_id=self._session_id),
            )

    def _handle_recognition_complete(self) -> None:
        """处理 DashScope Recognition 完成事件。"""

        with self._lock:
            if not self._final_text and self._latest_partial_text:
                self._final_text = self._latest_partial_text
            if self._completed_at_ms is None:
                self._completed_at_ms = int(time.time() * 1000)
            self._dashscope_last_package_delay_ms = self._read_recognition_metric("get_last_package_delay")
        self._done_event.set()

    def _handle_recognition_error(self, result: Any) -> None:
        """处理 DashScope Recognition 错误事件。"""

        message = str(read_attr_or_key(result, "message") or result)
        request_id = str(read_attr_or_key(result, "request_id") or "")
        self._error = build_error(
            ErrorCode.INTERNAL_ERROR,
            "实时 ASR 服务返回失败事件",
            details={
                "segment_id": self._segment_id,
                "stream_id": self._stream_id,
                "request_id": request_id,
                "message": message,
            },
        )
        with self._lock:
            if self._completed_at_ms is None:
                self._completed_at_ms = int(time.time() * 1000)
        self._done_event.set()
