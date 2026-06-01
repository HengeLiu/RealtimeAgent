#!/usr/bin/env python3
"""轻量独立 VAD 实验服务。

测试目标：接收 iOS 实验 App 上传的 WAV，判断是否存在明显语音段。
测试方法：可把 WAV 转发给百炼 Qwen-ASR-Realtime 的 server_vad，也可使用本地 webrtcvad/RMS 兜底。
预期结果：返回 JSON，包含是否触发、语音帧数量、首个语音帧时间和简单统计。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import queue
import statistics
import threading
import time
import uuid
import wave
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from typing import Any
from urllib.parse import parse_qs, urlparse

from pcm16_utils import pcm16_rms, pcm16_to_mono, resample_pcm16_mono


@dataclass(frozen=True)
class VADResult:
    """VAD 分析结果。"""

    triggered: bool
    speech_frames: int
    total_frames: int
    first_speech_ms: int | None
    speech_ratio: float
    rms_floor: float
    rms_threshold: float
    backend: str


@dataclass(frozen=True)
class WAVAudio:
    """WAV 解析结果。"""

    pcm16_mono: bytes
    sample_rate: int
    duration_ms: int


@dataclass
class RealtimeVADEvent:
    """实时 VAD 事件。"""

    seq: int
    type: str
    text: str | None
    message: str | None
    audio_ms: int | None
    recorded_at_ms: int
    payload: dict[str, Any]


class RealtimeVADRemoteError(RuntimeError):
    """远端实时 VAD 服务返回的错误。"""


class RealtimeVADSession:
    """持续接收端侧 PCM chunk，并转发给实时 VAD/ASR 服务。

    主要功能：维护一个服务端实时会话，端侧每次上传 AEC 后的 PCM chunk 后即可轮询新增事件。
    主要属性：`events` 保存已归一化事件；`chunks` 是音频发送队列；`worker` 负责远端 WebSocket。
    """

    def __init__(
        self,
        session_id: str,
        backend: str,
        api_key: str,
        model: str,
        timeout_sec: float,
        sample_rate: int,
        vad_threshold: float,
        silence_duration_ms: int,
    ) -> None:
        self.session_id = session_id
        self.backend = backend
        self.api_key = api_key
        self.model = model
        self.timeout_sec = timeout_sec
        self.sample_rate = sample_rate
        self.vad_threshold = vad_threshold
        self.silence_duration_ms = silence_duration_ms
        self.chunks: queue.Queue[bytes | None] = queue.Queue()
        self.events: list[RealtimeVADEvent] = []
        self.lock = threading.Lock()
        self.stop_requested = threading.Event()
        self.ready_event = threading.Event()
        self.started = False
        self.speech_active = False
        self.worker = threading.Thread(target=self._run, name=f"vad-{session_id}", daemon=True)
        self.worker.start()

    def wait_until_ready(self, timeout_sec: float) -> bool:
        """等待远端实时会话真正可用。

        主要逻辑：DashScope WebSocket 连接和 `session.update` 成功后才视为 ready；
        如果后台线程先记录 error，也会立刻唤醒调用方。
        参数：`timeout_sec` 是最多等待秒数。
        返回值：会话已经成功 started 时返回 True；出错或超时返回 False。
        异常情况：本函数不抛异常，错误详情通过 `latest_error_message` 读取。
        """

        if not self.ready_event.wait(timeout_sec):
            return False
        return self.latest_error_message() is None

    def latest_error_message(self) -> str | None:
        """返回当前会话最近一次错误消息。"""

        with self.lock:
            for event in reversed(self.events):
                if event.type == "error":
                    return event.message or event.text or json.dumps(_compact_payload(event.payload), ensure_ascii=False)
        return None

    def append_chunk(self, chunk: bytes) -> None:
        """追加一个端侧实时 PCM chunk。"""

        self.chunks.put(chunk)

    def finish(self) -> None:
        """请求实时会话结束。"""

        self.stop_requested.set()
        self.chunks.put(None)

    def get_events_after(self, seq: int) -> list[dict[str, Any]]:
        """返回指定序号之后的事件。"""

        with self.lock:
            return [event.__dict__ for event in self.events if event.seq > seq]

    def _record_event(self, event_type: str, payload: dict[str, Any], text: str | None = None) -> None:
        audio_ms = _event_audio_ms(payload)
        message = _event_message(payload)
        with self.lock:
            event = RealtimeVADEvent(
                seq=len(self.events),
                type=event_type,
                text=text,
                message=message,
                audio_ms=audio_ms,
                recorded_at_ms=int(time.time() * 1000),
                payload=payload,
            )
            self.events.append(event)
            if event_type in {"session.started", "error"}:
                self.ready_event.set()
        if event_type == "error":
            payload_text = json.dumps(_compact_payload(payload), ensure_ascii=False)
            print(
                f"[realtime-vad] session={self.session_id} event={event_type} "
                f"message={message!r} payload={payload_text[:800]}"
            )
        elif event_type == "asr_text":
            payload_keys = ",".join(sorted(payload.keys()))
            print(
                f"[realtime-vad] session={self.session_id} event={event_type} "
                f"text={text!r} audio_ms={audio_ms} payload_keys={payload_keys}"
            )
        elif event_type == "asr_event":
            payload_text = json.dumps(_compact_payload(payload), ensure_ascii=False)
            print(f"[realtime-vad] session={self.session_id} event={event_type} payload={payload_text[:800]}")
        elif text:
            print(f"[realtime-vad] session={self.session_id} event={event_type} text={text}")
        else:
            print(f"[realtime-vad] session={self.session_id} event={event_type} audio_ms={audio_ms}")

    def _run(self) -> None:
        try:
            if self.backend == "dashscope":
                self._run_dashscope()
            else:
                self._run_local()
        except RealtimeVADRemoteError:
            return
        except Exception as exc:  # noqa: BLE001 - 实验服务需要把实时错误返回端侧。
            self._record_event("error", {"error": str(exc)})

    def _run_dashscope(self) -> None:
        try:
            import websocket  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("缺少 websocket-client，请先安装 websocket-client") from exc

        if not self.api_key:
            raise RuntimeError("实时 VAD 需要 DASHSCOPE_API_KEY")
        url = f"wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model={self.model}"
        headers = [
            f"Authorization: Bearer {_normalize_dashscope_api_key(self.api_key)}",
            "OpenAI-Beta: realtime=v1",
        ]
        ws = websocket.create_connection(url, header=headers, timeout=self.timeout_sec)
        ws.settimeout(self.timeout_sec)
        try:
            run_id = f"rt_{self.session_id}"
            ws.send(json.dumps({
                "event_id": f"{run_id}_session_update",
                "type": "session.update",
                "session": {
                    "modalities": ["text"],
                    "input_audio_format": "pcm",
                    "sample_rate": self.sample_rate,
                    "input_audio_transcription": {
                        "language": "zh",
                    },
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": self.vad_threshold,
                        "silence_duration_ms": self.silence_duration_ms,
                    },
                },
            }, ensure_ascii=False))
            deadline = time.monotonic() + self.timeout_sec
            while time.monotonic() < deadline:
                event = json.loads(ws.recv())
                self._handle_dashscope_event(event)
                if event.get("type") == "session.updated":
                    break

            self._record_event("session.started", {"backend": "dashscope_realtime_server_vad", "model": self.model})
            while not self.stop_requested.is_set():
                try:
                    chunk = self.chunks.get(timeout=0.05)
                except queue.Empty:
                    self._drain_dashscope(ws)
                    continue
                if chunk is None:
                    break
                ws.send(json.dumps({
                    "event_id": f"{run_id}_append_{uuid.uuid4().hex[:8]}",
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(chunk).decode("ascii"),
                }))
                self._drain_dashscope(ws)

            ws.send(json.dumps({"event_id": f"{run_id}_commit", "type": "input_audio_buffer.commit"}))
            ws.send(json.dumps({"event_id": f"{run_id}_finish", "type": "session.finish"}))
            finish_deadline = time.monotonic() + min(self.timeout_sec, 5.0)
            while time.monotonic() < finish_deadline:
                if self._drain_dashscope(ws):
                    break
                time.sleep(0.02)
        finally:
            ws.close()
            self._record_event("session.finished", {"backend": self.backend})

    def _drain_dashscope(self, ws: Any) -> bool:
        while True:
            ws.settimeout(0.001)
            try:
                event = json.loads(ws.recv())
            except Exception:
                ws.settimeout(self.timeout_sec)
                return False
            if self._handle_dashscope_event(event):
                ws.settimeout(self.timeout_sec)
                return True

    def _handle_dashscope_event(self, event: dict[str, Any]) -> bool:
        event_type = event.get("type")
        if event_type == "error":
            if _is_empty_commit_error(event):
                message = _event_message(event) or "empty commit ignored"
                print(f"[realtime-vad] session={self.session_id} event=finish.empty_commit_ignored message={message!r}")
                return False
            self._record_event("error", event)
            raise RealtimeVADRemoteError(_event_message(event) or "DashScope realtime VAD error")
        elif event_type == "input_audio_buffer.speech_started":
            self._record_event("speech_started", event)
        elif event_type == "input_audio_buffer.speech_stopped":
            self._record_event("speech_stopped", event)
        elif event_type and ("transcription" in event_type or event_type.endswith(".completed")):
            texts = [text for text in _extract_text_values(event) if text.strip()]
            if texts:
                for text in texts:
                    self._record_event("asr_text", event, text=text)
            else:
                self._record_event("asr_event", event)
        return event_type == "session.finished"

    def _run_local(self) -> None:
        self._record_event("session.started", {"backend": "local_rms_realtime"})
        while True:
            chunk = self.chunks.get()
            if chunk is None:
                break
            rms = pcm16_rms(chunk)
            is_speech = rms > 450
            if is_speech and not self.speech_active:
                self.speech_active = True
                self._record_event("speech_started", {"backend": "local_rms_realtime", "rms": rms})
            elif not is_speech and self.speech_active:
                self.speech_active = False
                self._record_event("speech_stopped", {"backend": "local_rms_realtime", "rms": rms})
        self._record_event("session.finished", {"backend": "local_rms_realtime"})


class RealtimeVADRegistry:
    """实时 VAD 会话注册表。"""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sessions: dict[str, RealtimeVADSession] = {}

    def create(
        self,
        backend: str,
        api_key: str,
        model: str,
        timeout_sec: float,
        sample_rate: int,
        vad_threshold: float,
        silence_duration_ms: int,
    ) -> RealtimeVADSession:
        session_id = f"rt_vad_{uuid.uuid4().hex[:12]}"
        session = RealtimeVADSession(
            session_id,
            backend,
            api_key,
            model,
            timeout_sec,
            sample_rate,
            vad_threshold,
            silence_duration_ms,
        )
        with self.lock:
            self.sessions[session_id] = session
        return session

    def get(self, session_id: str) -> RealtimeVADSession | None:
        with self.lock:
            return self.sessions.get(session_id)

    def remove(self, session_id: str) -> None:
        with self.lock:
            session = self.sessions.pop(session_id, None)
        if session:
            session.finish()


def read_wav_pcm16_mono(data: bytes) -> WAVAudio:
    """读取 WAV 并转成单声道 PCM16。

    主要逻辑：解析 WAV 头，必要时把多声道平均成单声道。
    参数：`data` 是 WAV 字节。
    返回值：单声道 PCM16、采样率和时长。
    异常情况：非 16-bit PCM WAV 时抛出 ValueError。
    """

    with wave.open(BytesIO(data), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())
        frame_count = wav.getnframes()

    if sample_width != 2:
        raise ValueError(f"只支持 16-bit PCM WAV，当前 sample_width={sample_width}")
    if channels != 1:
        frames = pcm16_to_mono(frames, channels)
    duration_ms = int(round(frame_count * 1000 / max(1, sample_rate)))
    return WAVAudio(pcm16_mono=frames, sample_rate=sample_rate, duration_ms=duration_ms)


def analyze_wav(data: bytes, aggressive: int) -> VADResult:
    """分析 WAV 字节并返回 VAD 结果。

    主要逻辑：读取 16-bit PCM WAV，切成 20ms 帧；如果本地安装了 webrtcvad 就直接使用，
    否则用前几帧估计噪声底，再以 RMS 阈值判断明显语音段。
    参数：`data` 是 WAV 字节；`aggressive` 是 webrtcvad 激进度，取值 0-3。
    返回值：结构化 VAD 结果。
    异常情况：非 WAV 或非 16-bit PCM 时抛出 ValueError。
    """

    audio = read_wav_pcm16_mono(data)
    sample_width = 2
    sample_rate = audio.sample_rate
    frames = audio.pcm16_mono

    frame_ms = 20
    samples_per_frame = max(1, int(sample_rate * frame_ms / 1000))
    bytes_per_frame = samples_per_frame * sample_width
    chunks = [
        frames[offset : offset + bytes_per_frame]
        for offset in range(0, len(frames) - bytes_per_frame + 1, bytes_per_frame)
    ]
    if not chunks:
        return VADResult(False, 0, 0, None, 0.0, 0.0, 0.0, "empty")

    try:
        import webrtcvad  # type: ignore

        vad = webrtcvad.Vad(max(0, min(3, aggressive)))
        speech_flags = [vad.is_speech(chunk, sample_rate) for chunk in chunks]
        backend = "webrtcvad"
        rms_values = [pcm16_rms(chunk) for chunk in chunks]
        floor = statistics.median(rms_values[: min(10, len(rms_values))])
        threshold = 0.0
    except Exception:
        rms_values = [pcm16_rms(chunk) for chunk in chunks]
        floor = statistics.median(rms_values[: min(15, len(rms_values))])
        spread = statistics.pstdev(rms_values[: min(30, len(rms_values))]) if len(rms_values) > 1 else 0.0
        threshold = max(350.0, floor * 3.0, floor + spread * 4.0)
        speech_flags = [rms > threshold for rms in rms_values]
        backend = "rms"

    speech_frames = sum(1 for flag in speech_flags if flag)
    first = next((index for index, flag in enumerate(speech_flags) if flag), None)
    speech_ratio = speech_frames / max(1, len(speech_flags))
    triggered = speech_frames >= 3 and speech_ratio >= 0.03
    return VADResult(
        triggered=triggered,
        speech_frames=speech_frames,
        total_frames=len(speech_flags),
        first_speech_ms=None if first is None else first * frame_ms,
        speech_ratio=speech_ratio,
        rms_floor=floor,
        rms_threshold=threshold,
        backend=backend,
    )


def analyze_wav_with_dashscope(
    data: bytes,
    api_key: str,
    model: str,
    timeout_sec: float,
    vad_threshold: float,
    silence_duration_ms: int,
) -> dict[str, Any]:
    """调用百炼 Qwen-ASR-Realtime server_vad 分析 WAV。

    主要逻辑：把 WAV 转成 16k 单声道 PCM16，按 100ms 分片发送到 Realtime WebSocket，
    收集 `speech_started`、`speech_stopped` 和转写事件，并打印原始事件摘要。
    参数：`data` 是 WAV 字节；`api_key` 是百炼 API Key；`model` 是 realtime ASR 模型名。
    返回值：兼容 iOS 现有响应字段的 JSON 字典，并附带 `dashscope_events`。
    异常情况：缺少依赖、鉴权失败、WebSocket 超时或服务端错误时抛出异常。
    """

    try:
        import websocket  # type: ignore
    except Exception as exc:  # noqa: BLE001 - 明确把依赖问题反馈到实验端。
        raise RuntimeError("缺少 websocket-client，请先安装 websocket-client") from exc

    audio = read_wav_pcm16_mono(data)
    pcm16_16k = resample_pcm16_mono(audio.pcm16_mono, audio.sample_rate, 16_000)
    chunk_ms = 100
    bytes_per_chunk = int(16_000 * chunk_ms / 1000) * 2
    url = f"wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model={model}"
    headers = [
        f"Authorization: Bearer {api_key}",
        "OpenAI-Beta: realtime=v1",
    ]
    run_id = f"vad_{uuid.uuid4().hex[:10]}"
    started_events: list[dict[str, Any]] = []
    stopped_events: list[dict[str, Any]] = []
    transcription_events: list[dict[str, Any]] = []
    raw_events: list[dict[str, Any]] = []

    print(f"[dashscope-vad] connect model={model} run_id={run_id} duration_ms={audio.duration_ms}")
    ws = websocket.create_connection(url, header=headers, timeout=timeout_sec)
    ws.settimeout(timeout_sec)
    try:
        session_update = {
            "event_id": f"{run_id}_session_update",
            "type": "session.update",
                "session": {
                    "modalities": ["text"],
                    "input_audio_format": "pcm",
                    "sample_rate": 16_000,
                    "input_audio_transcription": {
                        "language": "zh",
                    },
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": vad_threshold,
                    "silence_duration_ms": silence_duration_ms,
                },
            },
        }
        ws.send(json.dumps(session_update, ensure_ascii=False))
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            try:
                event = json.loads(ws.recv())
            except Exception:
                break
            raw_events.append(event)
            print(f"[dashscope-vad] event type={event.get('type')} payload={json.dumps(event, ensure_ascii=False)[:500]}")
            if event.get("type") == "error":
                raise RuntimeError(json.dumps(event.get("error", event), ensure_ascii=False))
            if event.get("type") in {"session.created", "session.updated"}:
                if event.get("type") == "session.updated":
                    break

        for offset in range(0, len(pcm16_16k), bytes_per_chunk):
            chunk = pcm16_16k[offset : offset + bytes_per_chunk]
            if not chunk:
                continue
            ws.send(
                json.dumps(
                    {
                        "event_id": f"{run_id}_append_{offset}",
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(chunk).decode("ascii"),
                    }
                )
            )
            # 百炼 Realtime 是流式接口，这里按真实时间发送，避免一次性灌入影响 server_vad 判断。
            time.sleep(chunk_ms / 1000)
            _drain_realtime_events(ws, raw_events, started_events, stopped_events, transcription_events)

        ws.send(json.dumps({"event_id": f"{run_id}_commit", "type": "input_audio_buffer.commit"}))
        ws.send(json.dumps({"event_id": f"{run_id}_finish", "type": "session.finish"}))
        finish_deadline = time.monotonic() + timeout_sec
        while time.monotonic() < finish_deadline:
            if _drain_realtime_events(ws, raw_events, started_events, stopped_events, transcription_events):
                break
            time.sleep(0.05)
    finally:
        ws.close()

    first_speech_ms = _event_audio_ms(started_events[0]) if started_events else None
    speech_frames = max(len(started_events), len(stopped_events))
    total_frames = max(1, int(round(audio.duration_ms / 20)))
    speech_ratio = speech_frames / total_frames
    payload = {
        "triggered": bool(started_events),
        "speech_frames": speech_frames,
        "total_frames": total_frames,
        "first_speech_ms": first_speech_ms,
        "speech_ratio": speech_ratio,
        "rms_floor": 0.0,
        "rms_threshold": 0.0,
        "backend": "dashscope_realtime_server_vad",
        "dashscope_model": model,
        "speech_started_count": len(started_events),
        "speech_stopped_count": len(stopped_events),
        "asr_texts": _extract_asr_texts(transcription_events),
        "dashscope_speech_started": started_events,
        "dashscope_speech_stopped": stopped_events,
        "dashscope_transcriptions": transcription_events,
        "dashscope_event_types": [event.get("type") for event in raw_events],
    }
    print("[dashscope-vad] result", json.dumps(payload, ensure_ascii=False))
    return payload


def _drain_realtime_events(
    ws: Any,
    raw_events: list[dict[str, Any]],
    started_events: list[dict[str, Any]],
    stopped_events: list[dict[str, Any]],
    transcription_events: list[dict[str, Any]],
) -> bool:
    """尽量读取当前可用的 Realtime 事件。

    返回值：收到 `session.finished` 时返回 True。
    """

    while True:
        ws.settimeout(0.001)
        try:
            event = json.loads(ws.recv())
        except Exception:
            ws.settimeout(10)
            return False
        event_type = event.get("type")
        raw_events.append(event)
        print(f"[dashscope-vad] event type={event_type} payload={json.dumps(event, ensure_ascii=False)[:500]}")
        if event_type == "input_audio_buffer.speech_started":
            started_events.append(event)
        elif event_type == "input_audio_buffer.speech_stopped":
            stopped_events.append(event)
        elif event_type and ("transcription" in event_type or event_type.endswith(".completed")):
            transcription_events.append(event)
            for text in _extract_text_values(event):
                print(f"[dashscope-vad] asr_text={text}")
        elif event_type == "session.finished":
            ws.settimeout(10)
            return True


def _extract_asr_texts(events: list[dict[str, Any]]) -> list[str]:
    """从 Realtime 转写事件里提取去重后的文本。

    主要逻辑：不同模型事件结构可能把文本放在 `text`、`transcript` 或嵌套内容中，
    因此递归扫描常见字段，并保持原始出现顺序。
    参数：`events` 是服务端收集到的转写事件。
    返回值：按出现顺序去重后的 ASR 文本列表。
    异常情况：事件结构不含文本时返回空列表。
    """

    texts: list[str] = []
    seen: set[str] = set()
    for event in events:
        for text in _extract_text_values(event):
            normalized = text.strip()
            if normalized and normalized not in seen:
                texts.append(normalized)
                seen.add(normalized)
    return texts


def _extract_text_values(value: Any) -> list[str]:
    """递归提取事件中的文本字段。"""

    text_keys = {
        "text",
        "transcript",
        "content",
        "delta",
        "sentence",
        "result",
        "final_text",
        "partial",
        "transcription",
    }
    results: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in text_keys and isinstance(item, str):
                results.append(item)
            else:
                results.extend(_extract_text_values(item))
    elif isinstance(value, list):
        for item in value:
            results.extend(_extract_text_values(item))
    return results


def _compact_payload(value: Any) -> Any:
    """压缩 Realtime 事件日志，避免把大块音频或重复字段刷到终端。"""

    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for key, item in value.items():
            if key == "audio":
                compact[key] = f"<base64:{len(item)}>" if isinstance(item, str) else "<audio>"
            else:
                compact[key] = _compact_payload(item)
        return compact
    if isinstance(value, list):
        return [_compact_payload(item) for item in value[:8]]
    if isinstance(value, str) and len(value) > 240:
        return value[:240] + "..."
    return value


def _event_audio_ms(event: dict[str, Any]) -> int | None:
    """从百炼 Realtime 事件里提取语音开始时间。"""

    for key in ("audio_start_ms", "audio_start", "start_ms", "offset_ms"):
        value = event.get(key)
        if isinstance(value, (int, float)):
            return int(value)
    return None


def _event_message(event: dict[str, Any]) -> str | None:
    """从 Realtime 错误或普通事件中提取可读消息。"""

    direct = event.get("message")
    if isinstance(direct, str) and direct:
        return direct
    error = event.get("error")
    if isinstance(error, dict):
        for key in ("message", "code", "type"):
            value = error.get(key)
            if isinstance(value, str) and value:
                return value
        return json.dumps(error, ensure_ascii=False)
    if isinstance(error, str) and error:
        return error
    return None


def _is_empty_commit_error(event: dict[str, Any]) -> bool:
    """判断 DashScope finish 阶段的空 buffer commit 是否可忽略。

    主要逻辑：实时 VAD 会话结束时，本地服务会做一次 `input_audio_buffer.commit` 收尾；
    如果远端当前没有待提交音频，DashScope 会返回 invalid_request_error。这个错误只说明
    收尾时没有有效音频 buffer，不代表实时 VAD 主链路失败。
    参数：`event` 是 DashScope Realtime 返回的 error 事件。
    返回值：属于可忽略的空提交错误时返回 True。
    异常情况：不会抛异常。
    """

    message = (_event_message(event) or "").lower()
    return (
        "committing input audio buffer" in message
        and "invalid audio stream" in message
    )


def _normalize_dashscope_api_key(api_key: str) -> str:
    """规范化 DashScope API Key。

    主要逻辑：命令行环境变量有时会误填成 `Bearer sk-...`，而 WebSocket header 组装时
    已经会添加 `Bearer`，这里提前剥离前缀，避免生成 `Bearer Bearer ...`。
    参数：`api_key` 是原始环境变量值。
    返回值：去掉空白和 Bearer 前缀后的 token。
    异常情况：不抛异常；空值由调用处继续判断。
    """

    token = api_key.strip()
    if token.lower().startswith("bearer "):
        return token.split(None, 1)[1].strip()
    return token


def _dashscope_key_fingerprint(api_key: str) -> str:
    """返回 API Key 的非敏感指纹，便于确认服务端实际加载了哪个 key。"""

    token = _normalize_dashscope_api_key(api_key)
    if not token:
        return "empty"
    if len(token) <= 8:
        return f"len={len(token)}"
    return f"len={len(token)} prefix={token[:4]} suffix={token[-4:]}"


class VADHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器。"""

    aggressive = 2
    backend = "local"
    dashscope_api_key = ""
    dashscope_model = "qwen3-asr-flash-realtime"
    dashscope_timeout_sec = 10.0
    dashscope_vad_threshold = 0.0
    dashscope_silence_duration_ms = 400
    realtime_registry = RealtimeVADRegistry()

    def do_GET(self) -> None:
        """返回健康检查。"""

        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_json({"ok": True})
            return
        if parsed.path.startswith("/vad/realtime/sessions/") and parsed.path.endswith("/events"):
            self._handle_realtime_events(parsed.path, parsed.query)
            return
        if self.path != "/health":
            self.send_error(404)
            return

    def do_POST(self) -> None:
        """处理 `/vad/analyze` WAV 上传请求。"""

        parsed = urlparse(self.path)
        if parsed.path == "/vad/realtime/sessions":
            self._handle_realtime_create()
            return
        if parsed.path.startswith("/vad/realtime/sessions/") and parsed.path.endswith("/chunks"):
            self._handle_realtime_chunk(parsed.path)
            return
        if parsed.path.startswith("/vad/realtime/sessions/") and parsed.path.endswith("/finish"):
            self._handle_realtime_finish(parsed.path)
            return
        if not parsed.path.startswith("/vad/analyze"):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length)
        try:
            if self.backend == "dashscope":
                payload = analyze_wav_with_dashscope(
                    data,
                    api_key=self.dashscope_api_key,
                    model=self.dashscope_model,
                    timeout_sec=self.dashscope_timeout_sec,
                    vad_threshold=self.dashscope_vad_threshold,
                    silence_duration_ms=self.dashscope_silence_duration_ms,
                )
            else:
                result = analyze_wav(data, self.aggressive)
                payload = result.__dict__
        except Exception as exc:  # noqa: BLE001 - 实验服务需要把错误直接反馈给手机端。
            self._send_json({"ok": False, "error": str(exc)}, status=400)
            return
        self._send_json({"ok": True, **payload})

    def _handle_realtime_create(self) -> None:
        """创建实时 VAD 会话。"""

        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        sample_rate = int(payload.get("sample_rate", 16_000))
        session = self.realtime_registry.create(
            backend=self.backend,
            api_key=self.dashscope_api_key,
            model=self.dashscope_model,
            timeout_sec=self.dashscope_timeout_sec,
            sample_rate=sample_rate,
            vad_threshold=self.dashscope_vad_threshold,
            silence_duration_ms=self.dashscope_silence_duration_ms,
        )
        ready_timeout_sec = min(6.0, self.dashscope_timeout_sec)
        if not session.wait_until_ready(ready_timeout_sec):
            error = session.latest_error_message() or f"realtime VAD session not ready after {ready_timeout_sec:.1f}s"
            self.realtime_registry.remove(session.session_id)
            self._send_json({
                "ok": False,
                "error": error,
                "backend": self.backend,
                "key_fingerprint": _dashscope_key_fingerprint(self.dashscope_api_key) if self.backend == "dashscope" else "",
            }, status=502)
            return
        self._send_json({
            "ok": True,
            "session_id": session.session_id,
            "backend": self.backend,
            "sample_rate": sample_rate,
            "vad_threshold": self.dashscope_vad_threshold,
            "silence_duration_ms": self.dashscope_silence_duration_ms,
        })

    def _handle_realtime_chunk(self, path: str) -> None:
        """接收一个实时 PCM chunk，并返回目前新增事件。"""

        session = self._lookup_realtime_session(path, suffix="/chunks")
        if session is None:
            self._send_json({"ok": False, "error": "realtime session not found"}, status=404)
            return
        after_seq = int(self.headers.get("X-After-Event-Seq", "-1"))
        length = int(self.headers.get("Content-Length", "0"))
        chunk = self.rfile.read(length)
        error = session.latest_error_message()
        if error:
            self._send_json({
                "ok": False,
                "error": error,
                "session_id": session.session_id,
                "events": session.get_events_after(after_seq),
            }, status=409)
            return
        if chunk:
            session.append_chunk(chunk)
        # 给 worker 一个很短的处理窗口，避免 HTTP 响应阻塞主播放链路。
        time.sleep(0.01)
        self._send_json({
            "ok": True,
            "session_id": session.session_id,
            "events": session.get_events_after(after_seq),
        })

    def _handle_realtime_events(self, path: str, query: str) -> None:
        """查询实时 VAD 会话事件。"""

        session = self._lookup_realtime_session(path, suffix="/events")
        if session is None:
            self._send_json({"ok": False, "error": "realtime session not found"}, status=404)
            return
        params = parse_qs(query)
        after_seq = int(params.get("after_seq", ["-1"])[0])
        self._send_json({
            "ok": True,
            "session_id": session.session_id,
            "events": session.get_events_after(after_seq),
        })

    def _handle_realtime_finish(self, path: str) -> None:
        """结束实时 VAD 会话。"""

        session = self._lookup_realtime_session(path, suffix="/finish")
        if session is None:
            self._send_json({"ok": False, "error": "realtime session not found"}, status=404)
            return
        session.finish()
        self._send_json({
            "ok": True,
            "session_id": session.session_id,
            "events": session.get_events_after(-1),
        })

    def _lookup_realtime_session(self, path: str, suffix: str) -> RealtimeVADSession | None:
        prefix = "/vad/realtime/sessions/"
        session_id = path[len(prefix) : -len(suffix)]
        return self.realtime_registry.get(session_id)

    def log_message(self, format: str, *args: Any) -> None:
        """打印简洁请求日志。"""

        print(f"{self.address_string()} {format % args}")

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    """启动 VAD 实验服务。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--aggressive", type=int, default=2)
    parser.add_argument("--backend", choices=["local", "dashscope"], default=os.getenv("VAD_BACKEND", "local"))
    parser.add_argument("--dashscope-model", default=os.getenv("DASHSCOPE_REALTIME_VAD_MODEL", "qwen3-asr-flash-realtime"))
    parser.add_argument("--dashscope-timeout-sec", type=float, default=float(os.getenv("DASHSCOPE_REALTIME_VAD_TIMEOUT_SEC", "10")))
    parser.add_argument(
        "--dashscope-vad-threshold",
        type=float,
        default=float(os.getenv("DASHSCOPE_REALTIME_VAD_THRESHOLD", "0.0")),
        help="百炼 realtime server_vad 阈值；越高越不敏感，默认 0.0。",
    )
    parser.add_argument(
        "--dashscope-silence-duration-ms",
        type=int,
        default=int(os.getenv("DASHSCOPE_REALTIME_VAD_SILENCE_MS", "400")),
        help="百炼 realtime server_vad 判定 speech_stopped 的静音时长，默认 400ms。",
    )
    args = parser.parse_args()
    VADHandler.aggressive = args.aggressive
    VADHandler.backend = args.backend
    VADHandler.dashscope_model = args.dashscope_model
    VADHandler.dashscope_timeout_sec = args.dashscope_timeout_sec
    VADHandler.dashscope_vad_threshold = args.dashscope_vad_threshold
    VADHandler.dashscope_silence_duration_ms = args.dashscope_silence_duration_ms
    VADHandler.dashscope_api_key = os.getenv("DASHSCOPE_API_KEY", "")
    if args.backend == "dashscope" and not VADHandler.dashscope_api_key:
        raise SystemExit("使用 --backend dashscope 时必须设置 DASHSCOPE_API_KEY")
    server = ThreadingHTTPServer((args.host, args.port), VADHandler)
    print(
        f"VAD server listening on http://{args.host}:{args.port}/vad/analyze "
        f"backend={args.backend} vad_threshold={args.dashscope_vad_threshold} "
        f"silence_duration_ms={args.dashscope_silence_duration_ms} "
        f"key={_dashscope_key_fingerprint(VADHandler.dashscope_api_key) if args.backend == 'dashscope' else '-'}"
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
