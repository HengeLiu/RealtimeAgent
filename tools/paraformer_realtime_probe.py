#!/usr/bin/env python3
"""Paraformer 实时识别探针。

功能：
  1. 使用 DashScope Python SDK 或原始 WebSocket 协议调用 paraformer-realtime-v2。
  2. 将所有回调、原始事件、句子字段、首包延迟和结束标记写入 JSONL。
  3. 帮助判断模型是否暴露 speech_start / speech_stop 事件，或只能通过识别结果推断。

主要逻辑：
  - SDK 模式复用 dashscope.audio.asr.Recognition，记录 RecognitionCallback 的所有事件。
  - WebSocket 模式按官方协议发送 run-task、音频二进制帧、finish-task，记录服务端返回的原始 JSON 事件。
  - 支持给输入前后补静音，便于观察 VAD 断句和任务保活行为。

参数：
  --input 输入音频文件。支持 16-bit PCM WAV，或裸 PCM。
  --mode sdk / websocket / both。
  --output-jsonl 事件输出路径。
  --summary-json 汇总输出路径。

异常：
  - 未设置 DASHSCOPE_API_KEY 时直接报错。
  - WebSocket 或 SDK 返回错误时记录错误事件并让进程非零退出。
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import time
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


DEFAULT_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"


@dataclass(frozen=True)
class AudioPayload:
    """一次实验要发送的音频数据。

    属性：
      data: 已经拼接前后静音后的 PCM 字节。
      sample_rate: 采样率。
      source_format: 原始输入格式说明。
    """

    data: bytes
    sample_rate: int
    source_format: str


class JsonlRecorder:
    """JSONL 记录器。

    主要方法：
      write: 追加一条事件并刷新文件。

    异常：
      文件无法创建或写入时向上抛出 OSError。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = path.open("w", encoding="utf-8")
        self.records: list[dict[str, Any]] = []
        self.started_at = time.monotonic()

    def write(self, event: str, **payload: Any) -> None:
        record = {
            "event": event,
            "monotonic_ms": round((time.monotonic() - self.started_at) * 1000, 3),
            **payload,
        }
        self.records.append(record)
        self._fp.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._fp.flush()

    def close(self) -> None:
        self._fp.close()


def load_audio(path: Path, sample_rate: int, leading_silence_ms: int, trailing_silence_ms: int) -> AudioPayload:
    """读取输入音频并补静音。

    主要逻辑：
      - WAV 输入要求 16-bit、单声道 PCM，采样率从文件头读取。
      - 其他扩展名按裸 PCM 处理，采样率使用命令行参数。
      - 静音按 16-bit mono PCM 生成。

    返回：
      AudioPayload，供 SDK 或 WebSocket 发送。

    异常：
      WAV 编码不符合要求或文件不存在时抛出 ValueError / OSError。
    """

    if path.suffix.lower() == ".wav":
        with wave.open(str(path), "rb") as wav:
            if wav.getnchannels() != 1:
                raise ValueError(f"WAV 必须是单声道: channels={wav.getnchannels()}")
            if wav.getsampwidth() != 2:
                raise ValueError(f"WAV 必须是 16-bit PCM: sampwidth={wav.getsampwidth()}")
            source_rate = wav.getframerate()
            data = wav.readframes(wav.getnframes())
        source_format = "wav-pcm16le"
    else:
        source_rate = sample_rate
        data = path.read_bytes()
        source_format = "raw-pcm16le"

    silence_prefix = make_silence(source_rate, leading_silence_ms)
    silence_suffix = make_silence(source_rate, trailing_silence_ms)
    return AudioPayload(
        data=silence_prefix + data + silence_suffix,
        sample_rate=source_rate,
        source_format=source_format,
    )


def make_silence(sample_rate: int, duration_ms: int) -> bytes:
    """生成 16-bit 单声道静音 PCM。"""

    if duration_ms <= 0:
        return b""
    samples = int(sample_rate * duration_ms / 1000)
    return b"\x00\x00" * samples


def chunks_by_ms(data: bytes, sample_rate: int, chunk_ms: int) -> Iterable[bytes]:
    """按毫秒切分 16-bit 单声道 PCM。"""

    frame_bytes = max(1, int(sample_rate * chunk_ms / 1000) * 2)
    for offset in range(0, len(data), frame_bytes):
        yield data[offset : offset + frame_bytes]


def sentence_from_sdk_result(result: Any) -> dict[str, Any] | None:
    """从 SDK RecognitionResult 中提取句子字典。

    SDK 对原始 WebSocket 事件做过封装，当前能稳定拿到的是
    output.sentence；若 SDK 后续变化导致字段不可读，返回 None。
    """

    getter = getattr(result, "get_sentence", None)
    if not callable(getter):
        return None
    try:
        sentence = getter()
    except Exception:
        return None
    return sentence if isinstance(sentence, dict) else None


def result_to_plain_dict(result: Any) -> dict[str, Any]:
    """尽量把 SDK Result 转成可 JSON 序列化的普通字典。"""

    plain: dict[str, Any] = {}
    for name in ("status_code", "request_id", "code", "message", "output", "usage", "usages"):
        if hasattr(result, name):
            value = getattr(result, name)
            try:
                json.dumps(value, ensure_ascii=False)
                plain[name] = value
            except TypeError:
                plain[name] = repr(value)
    return plain


def run_sdk(args: argparse.Namespace, audio: AudioPayload, recorder: JsonlRecorder) -> None:
    """使用 DashScope SDK 调用模型并记录回调事件。"""

    import dashscope
    from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult

    dashscope.api_key = os.environ["DASHSCOPE_API_KEY"]
    if args.websocket_url:
        dashscope.base_websocket_api_url = args.websocket_url

    events: "queue.Queue[tuple[str, dict[str, Any]]]" = queue.Queue()

    class Callback(RecognitionCallback):
        """把 SDK 回调原样转发到事件队列，避免在 SDK 线程里做复杂处理。"""

        def on_open(self) -> None:
            events.put(("sdk.open", {}))

        def on_close(self) -> None:
            events.put(("sdk.close", {}))

        def on_complete(self) -> None:
            events.put(("sdk.complete", {}))

        def on_error(self, result: RecognitionResult) -> None:
            events.put(("sdk.error", {"result": result_to_plain_dict(result)}))

        def on_event(self, result: RecognitionResult) -> None:
            sentence = sentence_from_sdk_result(result)
            events.put(
                (
                    "sdk.event",
                    {
                        "result": result_to_plain_dict(result),
                        "sentence": sentence,
                        "sentence_end_by_sdk": bool(sentence and RecognitionResult.is_sentence_end(sentence)),
                    },
                )
            )

    recognition = Recognition(
        model=args.model,
        callback=Callback(),
        format="pcm",
        sample_rate=audio.sample_rate,
        semantic_punctuation_enabled=args.semantic_punctuation_enabled,
        max_sentence_silence=args.max_sentence_silence,
        disfluency_removal_enabled=args.disfluency_removal_enabled,
        punctuation_prediction_enabled=args.punctuation_prediction_enabled,
        inverse_text_normalization_enabled=args.inverse_text_normalization_enabled,
        heartbeat=args.heartbeat,
    )

    recorder.write(
        "sdk.start",
        model=args.model,
        sample_rate=audio.sample_rate,
        audio_bytes=len(audio.data),
        source_format=audio.source_format,
    )
    recognition.start()
    drain_sdk_events(events, recorder)

    for index, chunk in enumerate(chunks_by_ms(audio.data, audio.sample_rate, args.chunk_ms)):
        recognition.send_audio_frame(chunk)
        recorder.write("sdk.audio_sent", seq=index, bytes=len(chunk))
        time.sleep(args.chunk_ms / 1000)
        drain_sdk_events(events, recorder)

    recognition.stop()
    deadline = time.monotonic() + args.wait_after_stop_seconds
    while time.monotonic() < deadline:
        drain_sdk_events(events, recorder)
        time.sleep(0.05)
    drain_sdk_events(events, recorder)
    recorder.write(
        "sdk.metrics",
        request_id=recognition.get_last_request_id(),
        first_package_delay_ms=recognition.get_first_package_delay(),
        last_package_delay_ms=recognition.get_last_package_delay(),
    )


def drain_sdk_events(events: "queue.Queue[tuple[str, dict[str, Any]]]", recorder: JsonlRecorder) -> None:
    """清空 SDK 回调队列。"""

    while True:
        try:
            event, payload = events.get_nowait()
        except queue.Empty:
            return
        recorder.write(event, **payload)


def run_websocket(args: argparse.Namespace, audio: AudioPayload, recorder: JsonlRecorder) -> None:
    """使用原始 WebSocket 协议调用模型并记录服务端事件。

    主要逻辑：
      - 发送 run-task 后等待 task-started。
      - 按固定 chunk_ms 发送音频，并在每次发送后尽量读取服务器事件。
      - 发送 finish-task 后继续读取到 task-finished。
    """

    import websocket

    api_key = os.environ["DASHSCOPE_API_KEY"]
    task_id = uuid.uuid4().hex
    ws = websocket.create_connection(
        args.websocket_url or DEFAULT_URL,
        header=[
            f"Authorization: Bearer {api_key}",
            "X-DashScope-DataInspection: disable",
        ],
        timeout=args.websocket_timeout_seconds,
    )
    ws.settimeout(0.05)
    recorder.write("ws.open", url=args.websocket_url or DEFAULT_URL, task_id=task_id)

    run_task = {
        "header": {"action": "run-task", "task_id": task_id, "streaming": "duplex"},
        "payload": {
            "task_group": "audio",
            "task": "asr",
            "function": "recognition",
            "model": args.model,
            "parameters": {
                "format": "pcm",
                "sample_rate": audio.sample_rate,
                "semantic_punctuation_enabled": args.semantic_punctuation_enabled,
                "max_sentence_silence": args.max_sentence_silence,
                "disfluency_removal_enabled": args.disfluency_removal_enabled,
                "punctuation_prediction_enabled": args.punctuation_prediction_enabled,
                "inverse_text_normalization_enabled": args.inverse_text_normalization_enabled,
                "heartbeat": args.heartbeat,
            },
            "input": {},
        },
    }
    ws.send(json.dumps(run_task, ensure_ascii=False))
    recorder.write("ws.command_sent", command="run-task", task_id=task_id)
    wait_for_event(ws, recorder, expected_event="task-started", timeout_seconds=args.websocket_timeout_seconds)

    for index, chunk in enumerate(chunks_by_ms(audio.data, audio.sample_rate, args.chunk_ms)):
        ws.send_binary(chunk)
        recorder.write("ws.audio_sent", seq=index, bytes=len(chunk))
        read_available_ws_events(ws, recorder)
        time.sleep(args.chunk_ms / 1000)

    finish_task = {
        "header": {"action": "finish-task", "task_id": task_id, "streaming": "duplex"},
        "payload": {"input": {}},
    }
    ws.send(json.dumps(finish_task, ensure_ascii=False))
    recorder.write("ws.command_sent", command="finish-task", task_id=task_id)
    wait_for_event(ws, recorder, expected_event="task-finished", timeout_seconds=args.websocket_timeout_seconds)
    ws.close()
    recorder.write("ws.close", task_id=task_id)


def wait_for_event(ws: Any, recorder: JsonlRecorder, *, expected_event: str, timeout_seconds: float) -> None:
    """等待指定 WebSocket 事件。"""

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            event = read_one_ws_event(ws, recorder)
        except TimeoutError:
            continue
        if event == expected_event:
            return
    raise TimeoutError(f"等待 {expected_event} 超时")


def read_available_ws_events(ws: Any, recorder: JsonlRecorder) -> None:
    """读取当前已经到达的 WebSocket 事件。"""

    while True:
        try:
            read_one_ws_event(ws, recorder)
        except TimeoutError:
            return


def read_one_ws_event(ws: Any, recorder: JsonlRecorder) -> str | None:
    """读取一条 WebSocket 文本事件并写入 JSONL。"""

    try:
        raw = ws.recv()
    except TimeoutError:
        raise
    except Exception as exc:
        if exc.__class__.__name__ in {"WebSocketTimeoutException", "TimeoutError"}:
            raise TimeoutError from exc
        raise
    if isinstance(raw, bytes):
        recorder.write("ws.binary_received", bytes=len(raw))
        return None
    try:
        message = json.loads(raw)
    except json.JSONDecodeError:
        recorder.write("ws.text_received", text=raw)
        return None
    header = message.get("header") or {}
    event = header.get("event")
    recorder.write("ws.event", service_event=event, message=message)
    return event if isinstance(event, str) else None


def build_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """从 JSONL 事件中生成用于判断链路能力的汇总。"""

    service_events: list[str] = []
    sdk_sentences: list[dict[str, Any]] = []
    ws_sentences: list[dict[str, Any]] = []
    first_text_event_ms: float | None = None
    first_sentence_begin_ms: float | None = None
    final_sentence_ms: float | None = None

    for record in records:
        event = record.get("event")
        sentence: dict[str, Any] | None = None
        if event == "sdk.event":
            sentence = record.get("sentence")
            if isinstance(sentence, dict):
                sdk_sentences.append(sentence)
        elif event == "ws.event":
            service_event = record.get("service_event")
            if isinstance(service_event, str):
                service_events.append(service_event)
            message = record.get("message") or {}
            sentence = (((message.get("payload") or {}).get("output") or {}).get("sentence"))
            if isinstance(sentence, dict):
                ws_sentences.append(sentence)
        if sentence and sentence.get("text") and first_text_event_ms is None:
            first_text_event_ms = record.get("monotonic_ms")
        if sentence and sentence.get("sentence_begin") is True and first_sentence_begin_ms is None:
            first_sentence_begin_ms = record.get("monotonic_ms")
        if sentence and (sentence.get("sentence_end") is True or sentence.get("end_time") is not None):
            current_ms = record.get("monotonic_ms")
            if final_sentence_ms is None or (isinstance(current_ms, (int, float)) and current_ms < final_sentence_ms):
                final_sentence_ms = current_ms

    all_sentences = sdk_sentences + ws_sentences
    return {
        "service_events_seen": sorted(set(service_events)),
        "contains_explicit_speech_start_event": any(
            "speech" in str(record).lower() and "start" in str(record).lower() for record in records
        ),
        "contains_explicit_speech_stop_event": any(
            "speech" in str(record).lower() and ("stop" in str(record).lower() or "end" in str(record).lower())
            for record in records
        ),
        "contains_sentence_begin_marker": any(sentence.get("sentence_begin") is True for sentence in all_sentences),
        "first_text_event_ms": first_text_event_ms,
        "first_sentence_begin_ms": first_sentence_begin_ms,
        "first_final_sentence_ms": final_sentence_ms,
        "sentence_count": len(all_sentences),
        "final_sentences": [
            sentence for sentence in all_sentences if sentence.get("sentence_end") is True or sentence.get("end_time") is not None
        ],
        "observed_sentence_keys": sorted({key for sentence in all_sentences for key in sentence.keys()}),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="探测 paraformer-realtime-v2 的实时事件与 VAD 断句行为")
    parser.add_argument("--input", type=Path, required=True, help="输入音频，支持 WAV 或裸 PCM")
    parser.add_argument("--mode", choices=["sdk", "websocket", "both"], default="both")
    parser.add_argument("--model", default="paraformer-realtime-v2")
    parser.add_argument("--sample-rate", type=int, default=16000, help="裸 PCM 输入采样率")
    parser.add_argument("--chunk-ms", type=int, default=100)
    parser.add_argument("--leading-silence-ms", type=int, default=500)
    parser.add_argument("--trailing-silence-ms", type=int, default=1200)
    parser.add_argument("--max-sentence-silence", type=int, default=800)
    parser.add_argument("--semantic-punctuation-enabled", action="store_true")
    parser.add_argument("--disfluency-removal-enabled", action="store_true")
    parser.add_argument("--no-punctuation-prediction", dest="punctuation_prediction_enabled", action="store_false")
    parser.set_defaults(punctuation_prediction_enabled=True)
    parser.add_argument("--no-itn", dest="inverse_text_normalization_enabled", action="store_false")
    parser.set_defaults(inverse_text_normalization_enabled=True)
    parser.add_argument("--heartbeat", action="store_true")
    parser.add_argument("--websocket-url", default=DEFAULT_URL)
    parser.add_argument("--websocket-timeout-seconds", type=float, default=20.0)
    parser.add_argument("--wait-after-stop-seconds", type=float, default=2.0)
    parser.add_argument("--output-jsonl", type=Path, default=Path("runs/paraformer-probe/events.jsonl"))
    parser.add_argument("--summary-json", type=Path, default=Path("runs/paraformer-probe/summary.json"))
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if not os.environ.get("DASHSCOPE_API_KEY"):
        print("DASHSCOPE_API_KEY 未设置，无法调用 DashScope 实时识别服务。", file=sys.stderr)
        return 2

    audio = load_audio(args.input, args.sample_rate, args.leading_silence_ms, args.trailing_silence_ms)
    recorder = JsonlRecorder(args.output_jsonl)
    try:
        recorder.write(
            "probe.input",
            input=str(args.input),
            mode=args.mode,
            model=args.model,
            sample_rate=audio.sample_rate,
            total_audio_bytes=len(audio.data),
            chunk_ms=args.chunk_ms,
            leading_silence_ms=args.leading_silence_ms,
            trailing_silence_ms=args.trailing_silence_ms,
            max_sentence_silence=args.max_sentence_silence,
        )
        if args.mode in {"sdk", "both"}:
            run_sdk(args, audio, recorder)
        if args.mode in {"websocket", "both"}:
            run_websocket(args, audio, recorder)
    finally:
        summary = build_summary(recorder.records)
        args.summary_json.parent.mkdir(parents=True, exist_ok=True)
        args.summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        recorder.write("probe.summary", summary=summary, summary_json=str(args.summary_json))
        recorder.close()
    print(f"JSONL: {args.output_jsonl}")
    print(f"Summary: {args.summary_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
