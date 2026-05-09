from __future__ import annotations

import argparse
import asyncio
import json
import math
import shutil
import sys
import time
import wave
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiohttp import ClientSession, WSMsgType

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.protocol import Event, StreamChunk, StreamChunkCodec, StreamFormat, new_id


PLAYBACK_REQUIRED_EVENTS = (
    "control.device.registered",
    "control.audio_session.open.requested",
    "control.audio_session.opened",
    "stream.input.opened",
    "agent.response.started",
    "stream.output.open.requested",
    "stream.output.started",
    "stream.output.close.requested",
    "stream.output.finished",
    "stream.output.closed",
    "control.audio_session.close.requested",
    "control.audio_session.closed",
)

PLAYBACK_ARTIFACT_FILES = (
    "events.jsonl",
    "stream-events.jsonl",
    "agent-events.jsonl",
    "tool-events.jsonl",
    "task-events.jsonl",
    "assets.jsonl",
    "output-decisions.jsonl",
    "actuators.jsonl",
    "result.json",
)

ASSET_STREAM_TYPES = {"sensor.rgb", "sensor.depth", "sensor.imu"}
DEFAULT_MIC_CHUNK_MS = 20

DEFAULT_SENSOR_PROFILES: dict[str, dict[str, Any]] = {
    "sensor.rgb": {
        "codec": "jpeg",
        "sample_rate": 1,
        "channels": 1,
        "chunk_ms": 1,
        "payload_prefix": b"\xff\xd8playback-rgb-frame-",
        "payload_suffix": b"\xff\xd9",
    },
    "sensor.depth": {
        "codec": "raw",
        "sample_rate": 1,
        "channels": 1,
        "chunk_ms": 1,
        "payload_prefix": b"playback-depth-frame-",
        "payload_suffix": b"",
    },
    "sensor.imu": {
        "codec": "raw",
        "sample_rate": 50,
        "channels": 6,
        "chunk_ms": 20,
        "payload_prefix": b"playback-imu-frame-",
        "payload_suffix": b"",
    },
}


@dataclass(frozen=True)
class PlaybackAudio:
    """Playback 输入音频。

    主要功能：保存从录音 WAV 或原始 PCM 解析出的麦克风输入。
    主要属性：`chunks` 是按端侧发送粒度切好的 PCM16 数据，`format` 是 stream
    声明格式，`source_path` 记录原始 WAV 路径用于回放产物追踪。
    """

    chunks: list[bytes]
    format: StreamFormat
    source_path: str = ""
    duration_ms: int = 0

    @property
    def total_bytes(self) -> int:
        """返回输入 PCM 总字节数。"""

        return sum(len(chunk) for chunk in self.chunks)

    @property
    def chunk_count(self) -> int:
        """返回输入 chunk 数量。"""

        return len(self.chunks)


def _repo_root() -> Path:
    """返回仓库根目录。

    主要逻辑：`playback.py` 位于 `device-examples/python-glass/audio_chat_python_glass`，
    向上三层是仓库根目录。
    返回值：仓库根目录路径。
    异常情况：无。
    """

    return Path(__file__).resolve().parents[3]


def _audio_chat_root() -> Path:
    """返回当前项目根目录。

    主要逻辑：历史版本存在 `audio-chat/` 子工程包裹目录；新版已经把 SDK、应用示例
    和端侧示例提到项目根目录。本函数保留旧名称，作为路径兼容入口。
    """

    return Path(__file__).resolve().parents[3]


def _resolve_existing_path(raw_path: str | Path) -> Path:
    """解析配置中的本地文件路径。

    主要逻辑：依次按绝对路径、当前工作目录、`audio-chat` 根目录和仓库根目录查找，
    兼容从仓库根目录或 `audio-chat` 目录启动 CLI。
    参数：`raw_path` 为 YAML/命令行中的路径。
    返回值：存在的绝对路径。
    异常情况：文件不存在时抛出 `FileNotFoundError`。
    """

    path = Path(raw_path).expanduser()
    candidates = [path]
    if not path.is_absolute():
        candidates.extend([Path.cwd() / path, _audio_chat_root() / path, _repo_root() / path])
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(f"playback audio file not found: {raw_path}")


def load_wav_audio(path: str | Path, *, chunk_ms: int = DEFAULT_MIC_CHUNK_MS) -> PlaybackAudio:
    """读取录制 WAV 并切成端侧麦克风 stream chunk。

    主要逻辑：
    1. 使用标准库 `wave` 读取本地 WAV。
    2. 只接受 16kHz、单声道、16bit PCM，和真实端侧上传给 server 的格式一致。
    3. 按 `chunk_ms` 切片，默认 20ms，即每包 640 字节。

    参数：
    1. `path`：WAV 文件路径，可为相对路径。
    2. `chunk_ms`：每个 stream chunk 对应的音频时长。

    返回值：`PlaybackAudio`。

    异常情况：
    1. 文件不存在时抛出 `FileNotFoundError`。
    2. WAV 格式不符合 16kHz/mono/16bit PCM 时抛出 `ValueError`。
    """

    source = _resolve_existing_path(path)
    with wave.open(str(source), "rb") as wav_file:
        sample_rate = wav_file.getframerate()
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        frame_count = wav_file.getnframes()
        if sample_rate != 16000 or channels != 1 or sample_width != 2:
            raise ValueError(
                f"playback wav must be 16kHz/mono/16bit PCM: path={source} "
                f"sample_rate={sample_rate} channels={channels} sample_width={sample_width}"
            )
        pcm = wav_file.readframes(frame_count)
    bytes_per_chunk = max(1, int(sample_rate * channels * sample_width * chunk_ms / 1000))
    chunks = [pcm[offset : offset + bytes_per_chunk] for offset in range(0, len(pcm), bytes_per_chunk)]
    chunks = [chunk for chunk in chunks if chunk]
    return PlaybackAudio(
        chunks=chunks,
        format=StreamFormat(codec="pcm16le", sample_rate=sample_rate, channels=channels, chunk_ms=chunk_ms),
        source_path=str(source),
        duration_ms=int(frame_count * 1000 / sample_rate),
    )


def _pcm_audio(payload: bytes | None, *, chunk_ms: int = DEFAULT_MIC_CHUNK_MS) -> PlaybackAudio:
    """把测试用 PCM bytes 归一成 PlaybackAudio。

    主要逻辑：历史测试直接传入一段 PCM bytes 的调用方式，并保持默认静音输入。
    参数：`payload` 为 PCM16 原始字节。
    返回值：`PlaybackAudio`。
    异常情况：无。
    """

    pcm = payload if payload is not None else b"\x00\x00" * 320
    return PlaybackAudio(
        chunks=[pcm],
        format=StreamFormat(codec="pcm16le", sample_rate=16000, channels=1, chunk_ms=chunk_ms),
        duration_ms=chunk_ms if pcm else 0,
    )


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """递归合并配置字典。"""

    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _bytes_from_config(value: Any, *, default: bytes) -> bytes:
    """从配置中读取 bytes，支持文本和十六进制字符串。"""

    if value is None:
        return default
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        if value.startswith("hex:"):
            return bytes.fromhex(value[4:])
        path = Path(value)
        if path.exists():
            return path.read_bytes()
        return value.encode("utf-8")
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def _audio_from_action(action: dict[str, Any]) -> PlaybackAudio | None:
    """从 playback action 中解析 WAV 输入。

    主要逻辑：支持 `audio_wav`、`wav_path` 和 `wav` 三种配置名，方便复用老 SDK
    音频样例路径。
    参数：`action` 为场景中的单个 action。
    返回值：命中 WAV 时返回 `PlaybackAudio`，否则返回 None。
    异常情况：WAV 不存在或格式不符合要求时由 `load_wav_audio` 抛出。
    """

    raw_path = action.get("audio_wav") or action.get("wav_path") or action.get("wav")
    if not raw_path:
        return None
    return load_wav_audio(raw_path, chunk_ms=int(action.get("chunk_ms") or DEFAULT_MIC_CHUNK_MS))


def _audio_result(audio: PlaybackAudio) -> dict[str, Any]:
    """构造回放结果中的输入音频摘要。"""

    return {
        "source_path": audio.source_path,
        "chunk_count": audio.chunk_count,
        "total_bytes": audio.total_bytes,
        "duration_ms": audio.duration_ms,
        "format": audio.format.__dict__,
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """读取 JSONL 文件，不存在时返回空列表。"""

    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class PythonPlaybackEndpoint:
    def __init__(
        self,
        *,
        app: AudioChatApp,
        user_id: str,
        device_id: str,
        sensor_profiles: dict[str, dict[str, Any]] | None = None,
        heading: dict[str, Any] | None = None,
        location: dict[str, Any] | None = None,
    ) -> None:
        self.app = app
        self.user_id = user_id
        self.device_id = device_id
        self.sensor_profiles = {
            stream_type: _deep_merge(DEFAULT_SENSOR_PROFILES[stream_type], dict((sensor_profiles or {}).get(stream_type) or {}))
            for stream_type in DEFAULT_SENSOR_PROFILES
        }
        self.heading = dict(heading or {})
        self.location = dict(location or {})
        self.events: list[Event] = []
        self.output_chunks: list[StreamChunk] = []
        self.actuator_events: list[dict[str, Any]] = []
        self.asset_uploads: list[dict[str, Any]] = []
        self._started_output_streams: set[str] = set()
        self._registered = False
        self._last_session_id: str | None = None

    def push_event(self, event: Event) -> None:
        self.events.append(event)
        if event.event_name == "control.audio_session.open.requested":
            self._last_session_id = event.session_id
            self.app.publish_control_event(
                Event(
                    event_name="control.audio_session.opened",
                    user_id=self.user_id,
                    producer_id=self.device_id,
                    session_id=self.device_id,
                    payload={"reason": "playback_opened"},
                )
            )
        elif event.event_name == "stream.output.close.requested":
            self._record_actuator_event(
                event.session_id,
                {
                    "event": "actuator.output.close.requested",
                    "stream_id": event.stream_id,
                    "stream_type": event.stream_type,
                    "reason": event.payload.get("reason"),
                },
            )
            self.app.publish_control_event(
                Event(
                    event_name="stream.output.finished",
                    user_id=self.user_id,
                    producer_id=self.device_id,
                    session_id=self.device_id,
                    stream_id=event.stream_id,
                    stream_type=event.stream_type,
                    payload={"stream_type": event.stream_type},
                )
            )
            self.app.publish_control_event(
                Event(
                    event_name="stream.output.closed",
                    user_id=self.user_id,
                    producer_id=self.device_id,
                    session_id=self.device_id,
                    stream_id=event.stream_id,
                    stream_type=event.stream_type,
                    payload={"stream_type": event.stream_type, "reason": "playback_closed"},
                )
            )
        elif event.event_name == "control.audio_session.close.requested":
            self.app.publish_control_event(
                Event(
                    event_name="control.audio_session.closed",
                    user_id=self.user_id,
                    producer_id=self.device_id,
                    session_id=self.device_id,
                    payload={"reason": "playback_closed"},
                )
            )
        elif event.event_name == "stream.control.open.requested" and event.stream_type in ASSET_STREAM_TYPES:
            self._handle_sensor_configure(event)

    def push_stream_chunk(self, chunk: StreamChunk) -> None:
        if chunk.stream_id not in self._started_output_streams:
            self._started_output_streams.add(chunk.stream_id)
            self.app.publish_control_event(
                Event(
                    event_name="stream.output.started",
                    user_id=self.user_id,
                    producer_id=self.device_id,
                    session_id=self.device_id,
                    stream_id=chunk.stream_id,
                    stream_type=chunk.stream_type,
                    payload={"stream_type": chunk.stream_type},
                )
            )
        self.output_chunks.append(chunk)
        self._record_actuator_event(
            chunk.session_id,
            {
                "event": "actuator.chunk.received",
                "stream_id": chunk.stream_id,
                "stream_type": chunk.stream_type,
                "seq": chunk.seq,
                "payload_size": len(chunk.payload),
                "final": chunk.final,
                "codec": chunk.codec,
            },
        )

    def run_once(
        self,
        audio_payload: bytes | None = None,
        *,
        audio: PlaybackAudio | None = None,
        chunk_interval_ms: int = 0,
    ) -> dict[str, Any]:
        """执行一次内存模式 playback。

        主要逻辑：模拟端侧唤醒、打开 `sensor.mic` stream，并把录制音频按 chunk
        写入 server；不绕过 AudioChatApp 的公开协议入口。
        参数：
        1. `audio_payload`：历史测试的一段 PCM bytes。
        2. `audio`：从 WAV 读取出的分片音频。
        3. `chunk_interval_ms`：可选发送间隔，用于模拟真实端侧节奏。
        返回值：结构化回放结果。
        异常情况：底层 app 处理异常会直接抛出，便于测试暴露问题。
        """

        self.register()
        self.app.publish_control_event(
            Event(
                event_name="control.user.wake.detected",
                user_id=self.user_id,
                producer_id=self.device_id,
                payload={"wake_source": "playback"},
            )
        )
        playback_audio = audio or _pcm_audio(audio_payload)
        handle = self.app.open_input_stream(user_id=self.user_id, producer_id=self.device_id, format=playback_audio.format)
        self._last_session_id = handle.session_id
        for seq, payload in enumerate(playback_audio.chunks):
            self.app.write_input_chunk(
                StreamChunk(
                    user_id=self.user_id,
                    session_id=handle.session_id,
                    stream_id=handle.stream_id,
                    stream_type="sensor.mic",
                    seq=seq,
                    payload=payload,
                    final=seq == len(playback_audio.chunks) - 1,
                    codec=playback_audio.format.codec,
                    sample_rate=playback_audio.format.sample_rate,
                    channels=playback_audio.format.channels,
                    duration_ms=playback_audio.format.chunk_ms,
                    metadata={"source_path": playback_audio.source_path} if playback_audio.source_path else {},
                )
            )
            if chunk_interval_ms > 0 and seq < len(playback_audio.chunks) - 1:
                time.sleep(chunk_interval_ms / 1000)
        self.app.stream_service.close_stream(handle.stream_id, reason="playback_input_done")
        self.app.close_audio_session(self.user_id, reason="mock_response_completed")
        result = self._build_result(handle.session_id)
        result["input_audio"] = _audio_result(playback_audio)
        self.app.recorder.record_playback_result(handle.session_id, result)
        self.app.recorder.write_result(
            handle.session_id,
            {"ok": result["passed"], "status": "ok" if result["passed"] else "failed", **result},
        )
        return result

    def register(self) -> Event:
        """注册 playback 设备。

        主要逻辑：声明可生产 mic/rgb/depth/imu，可消费 speaker/haptic，并订阅对应
        event，避免端侧按固定 glass/phone 类型建模。
        返回值：注册成功事件。
        异常情况：注册失败由 app 返回非 registered 事件，调用方可据此断言。
        """

        if self._registered:
            return self.events[0]
        registration = Event(
            event_name="control.device.register.requested",
            user_id=self.user_id,
            producer_id=self.device_id,
            payload={
                "device_id": self.device_id,
                "name": "Python 回放设备示例",
                "device_name": "python-playback",
                "client_type": "python-playback",
                "sdk_version": "audio-chat-endpoint-0.1.0",
                "auth": {"mode": "disabled"},
                "properties": {
                    "audio.wake_word": "endpoint",
                    "audio.aec": "endpoint",
                },
                "subscriptions": [
                    {"event": "control.audio_session.*"},
                    {"event": "stream.output.*", "filter": {"stream_type": "actuator.speaker"}},
                    {"event": "stream.output.*", "filter": {"stream_type": "actuator.haptic"}},
                    {"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}},
                    {"event": "stream.control.*", "filter": {"stream_type": "sensor.depth"}},
                    {"event": "stream.control.*", "filter": {"stream_type": "sensor.imu"}},
                ],
            },
        )
        registered = self.app.register_device(registration, self)
        self.events.append(registered)
        self._registered = registered.event_name == "control.device.registered"
        return registered

    def run_scripted(self, scenario: dict[str, Any]) -> dict[str, Any]:
        """执行配置化设备级回放场景。

        主要逻辑：按 `actions` 依次执行 trigger audio、Tool、Task、通知或 stream 配置，
        最后用内置断言 DSL 检查事件、stream、asset、tool、task 和 output 产物。
        参数：`scenario` 为 playback YAML/JSON 中的场景配置。
        返回值：结构化回放结果。
        异常情况：业务 Tool / Task 抛错时向上传递，便于验收快速失败。
        """

        self.register()
        session_id = self.app.active_session_id(self.user_id)
        self._last_session_id = session_id
        action_results: list[dict[str, Any]] = []
        actions = list(scenario.get("actions") or [])
        if not actions and scenario.get("trigger_audio", True):
            actions.append({"type": "trigger_audio"})
        for action in actions:
            action_type = str(action.get("type") or action.get("action") or "").strip()
            if action_type == "trigger_audio":
                audio = _audio_from_action(action)
                audio_payload = None if audio is not None else _bytes_from_config(action.get("payload"), default=b"\x00\x00" * 320)
                result = self.run_once(
                    audio_payload=audio_payload,
                    audio=audio,
                    chunk_interval_ms=int(action.get("chunk_interval_ms") or 0),
                )
                session_id = result.get("session_id") or session_id
                action_results.append({"type": action_type, "session_id": session_id, "input_audio": result.get("input_audio")})
            elif action_type == "call_tool":
                result = asyncio.run(
                    self.app.tool_gateway.call(
                        name=str(action.get("name") or action.get("tool") or ""),
                        user_id=self.user_id,
                        session_id=session_id,
                        input_data=dict(action.get("input") or {}),
                    )
                )
                action_results.append(
                    {
                        "type": action_type,
                        "name": action.get("name") or action.get("tool"),
                        "ok": result.ok,
                        "asset_count": len(result.assets or []),
                    }
                )
            elif action_type == "start_task":
                ref = asyncio.run(
                    self.app.task_engine.create(
                        task_type=str(action.get("task_type") or ""),
                        user_id=self.user_id,
                        session_id=session_id,
                        input_data=dict(action.get("input") or {}),
                    )
                )
                action_results.append(
                    {
                        "type": action_type,
                        "task_type": ref.task_type,
                        "task_id": ref.task_id,
                        "state": ref.state,
                    }
                )
            elif action_type == "notify":
                self.app.output_service.submit_text(
                    user_id=self.user_id,
                    session_id=session_id,
                    text=str(action.get("text") or ""),
                    priority=str(action.get("priority") or "normal"),
                    ttl_seconds=int(action.get("ttl_seconds") or 0),
                )
                action_results.append({"type": action_type, "text": action.get("text")})
            elif action_type == "open_sensor_stream":
                self.app.control_service.publish_matching(
                    Event(
                        event_name="stream.control.open.requested",
                        user_id=self.user_id,
                        producer_id="server-main",
                        session_id=session_id,
                        stream_type=str(action.get("stream_type") or ""),
                        payload=dict(action.get("payload") or {}),
                    ),
                    selection=str(action.get("selection") or "first_available"),
                )
                action_results.append({"type": action_type, "stream_type": action.get("stream_type")})
            elif action_type:
                raise ValueError(f"unknown playback scenario action: {action_type}")
        result = self._build_result(session_id, assertion_spec=dict(scenario.get("assert") or {}))
        result["actions"] = action_results
        self.app.recorder.record_playback_result(session_id, result)
        self.app.recorder.write_result(
            session_id,
            {"ok": result["passed"], "status": "ok" if result["passed"] else "failed", **result},
        )
        return result

    def _handle_sensor_configure(self, event: Event) -> None:
        mode = str(event.payload.get("mode") or "single")
        if mode == "stop":
            self.asset_uploads.append(
                {"event": "sensor.configure.stop", "stream_type": event.stream_type, "correlation_id": event.payload.get("correlation_id")}
            )
            return
        count = self._sample_count(event)
        self._upload_sensor_stream(
            stream_type=event.stream_type or "",
            session_id=self.device_id,
            request_id=event.payload.get("request_id"),
            correlation_id=event.payload.get("correlation_id"),
            count=count,
        )

    def _sample_count(self, event: Event) -> int:
        payload = dict(event.payload)
        if payload.get("max_samples") is not None:
            return max(1, int(payload.get("max_samples") or 1))
        if payload.get("frame_limit") is not None:
            return max(1, int(payload.get("frame_limit") or 1))
        rate = float(payload.get("rate_hz") or payload.get("fps") or self.sensor_profiles[event.stream_type or "sensor.rgb"].get("sample_rate") or 1)
        duration = float(payload.get("duration_seconds") or payload.get("duration") or 0)
        if duration > 0:
            return max(1, int(math.ceil(rate * duration)))
        return 3 if payload.get("mode") == "continuous" else 1

    def _upload_sensor_stream(
        self,
        *,
        stream_type: str,
        session_id: str | None,
        request_id: str | None,
        correlation_id: str | None,
        count: int,
    ) -> None:
        profile = self.sensor_profiles.get(stream_type) or DEFAULT_SENSOR_PROFILES[stream_type]
        handle = self.app.open_input_stream(
            user_id=self.user_id,
            producer_id=self.device_id,
            stream_type=stream_type,
            format=StreamFormat(
                codec=str(profile.get("codec") or "raw"),
                sample_rate=int(profile.get("sample_rate") or 1),
                channels=int(profile.get("channels") or 1),
                chunk_ms=int(profile.get("chunk_ms") or 1),
            ),
        )
        if session_id and handle.session_id != session_id:
            handle.session_id = session_id
            self._last_session_id = session_id
        for seq in range(count):
            metadata: dict[str, Any] = {}
            if request_id:
                metadata["request_id"] = request_id
            if correlation_id:
                metadata["correlation_id"] = correlation_id
            if self.heading:
                metadata["heading"] = self.heading
            if self.location:
                metadata["location"] = self.location
            payload = self._sensor_payload(stream_type=stream_type, seq=seq, profile=profile)
            self.app.write_input_chunk(
                StreamChunk(
                    user_id=self.user_id,
                    session_id=handle.session_id,
                    stream_id=handle.stream_id,
                    stream_type=stream_type,
                    seq=seq,
                    payload=payload,
                    codec=handle.format.codec,
                    sample_rate=handle.format.sample_rate,
                    channels=handle.format.channels,
                    duration_ms=handle.format.chunk_ms,
                    final=seq == count - 1,
                    metadata=metadata,
                )
            )
        self.app.stream_service.close_stream(handle.stream_id, reason="playback_sensor_uploaded")
        self.asset_uploads.append(
            {
                "stream_id": handle.stream_id,
                "stream_type": stream_type,
                "request_id": request_id,
                "correlation_id": correlation_id,
                "sample_count": count,
            }
        )

    def _sensor_payload(self, *, stream_type: str, seq: int, profile: dict[str, Any]) -> bytes:
        if "payloads" in profile:
            payloads = list(profile.get("payloads") or [])
            if payloads:
                return _bytes_from_config(payloads[min(seq, len(payloads) - 1)], default=b"")
        prefix = _bytes_from_config(profile.get("payload_prefix"), default=b"")
        suffix = _bytes_from_config(profile.get("payload_suffix"), default=b"")
        if stream_type == "sensor.imu":
            body = json.dumps(
                {
                    "seq": seq,
                    "heading": self.heading,
                    "location": self.location,
                    "accel": [0.0, 0.0, 9.8],
                    "gyro": [0.0, 0.0, 0.0],
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        else:
            body = str(seq).encode("ascii")
        return prefix + body + suffix

    def _record_actuator_event(self, session_id: str | None, record: dict[str, Any]) -> None:
        if not session_id:
            return
        self.actuator_events.append(record)
        if hasattr(self.app.recorder, "record_actuator_event"):
            self.app.recorder.record_actuator_event(session_id, record)

    def _build_result(self, session_id: str, assertion_spec: dict[str, Any] | None = None) -> dict[str, Any]:
        self._ensure_artifacts(session_id)
        session_dir = self.app.recorder.session_dir(session_id)
        session_events_path = session_dir / "events.jsonl"
        session_event_names = [
            json.loads(line)["event_name"]
            for line in session_events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        event_names = [event.event_name for event in self.events] + [
            event_name for event_name in session_event_names if event_name not in {event.event_name for event in self.events}
        ]
        stream_records = _read_jsonl(session_dir / "stream-events.jsonl")
        tool_records = _read_jsonl(session_dir / "tool-events.jsonl")
        task_records = _read_jsonl(session_dir / "task-events.jsonl")
        asset_records = _read_jsonl(session_dir / "assets.jsonl")
        output_records = _read_jsonl(session_dir / "output-decisions.jsonl")
        result = {
            "user_id": self.user_id,
            "device_id": self.device_id,
            "session_id": session_id,
            "event_names": event_names,
            "endpoint_received_event_names": [event.event_name for event in self.events],
            "output_chunk_count": len(self.output_chunks),
            "output_bytes": sum(len(chunk.payload) for chunk in self.output_chunks),
            "asset_uploads": list(self.asset_uploads),
            "actuator_event_count": len(self.actuator_events),
            "stream_types": sorted({record.get("stream_type") for record in stream_records if record.get("stream_type")}),
            "asset_count": len(asset_records),
            "tool_event_count": len(tool_records),
            "task_event_count": len(task_records),
            "output_decision_count": len(output_records),
            "artifacts": {name: str(session_dir / name) for name in PLAYBACK_ARTIFACT_FILES},
        }
        result["assertions"] = self._evaluate_assertions(
            assertion_spec or {},
            result=result,
            tool_records=tool_records,
            task_records=task_records,
        )
        result["passed"] = all(result["assertions"].values())
        self.app.recorder.record_playback_result(session_id, result)
        self.app.recorder.write_result(session_id, {"ok": result["passed"], "status": "ok" if result["passed"] else "failed", **result})
        return result

    def _evaluate_assertions(
        self,
        spec: dict[str, Any],
        *,
        result: dict[str, Any],
        tool_records: list[dict[str, Any]],
        task_records: list[dict[str, Any]],
    ) -> dict[str, bool]:
        expected_events = list(spec.get("expected_events") or PLAYBACK_REQUIRED_EVENTS)
        expected_stream_types = list(spec.get("expected_stream_types") or ["sensor.mic", "actuator.speaker"])
        expected_tool_events = list(spec.get("expected_tool_events") or [])
        expected_task_events = list(spec.get("expected_task_events") or [])
        expected_asset_count = int(spec.get("expected_asset_count", 0))
        expected_output_chunks = int(spec.get("expected_output_chunks", 1))
        tool_text = "\n".join(json.dumps(record, ensure_ascii=False, sort_keys=True) for record in tool_records)
        task_text = "\n".join(json.dumps(record, ensure_ascii=False, sort_keys=True) for record in task_records)
        assertions: dict[str, bool] = {}
        for event_name in expected_events:
            assertions[f"event:{event_name}"] = event_name in result["event_names"]
        for stream_type in expected_stream_types:
            assertions[f"stream:{stream_type}"] = stream_type in result["stream_types"]
        for event_name in expected_tool_events:
            assertions[f"tool:{event_name}"] = event_name in tool_text
        for event_name in expected_task_events:
            assertions[f"task:{event_name}"] = event_name in task_text
        assertions["asset_count"] = result["asset_count"] >= expected_asset_count
        assertions["output_chunks"] = result["output_chunk_count"] >= expected_output_chunks
        assertions["artifact_files"] = all(Path(path).exists() for path in result["artifacts"].values())
        return assertions

    def _ensure_artifacts(self, session_id: str) -> None:
        session_dir = self.app.recorder.session_dir(session_id)
        for name in PLAYBACK_ARTIFACT_FILES:
            path = session_dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("" if name.endswith(".jsonl") else "{}\n", encoding="utf-8")


class NetworkPythonPlaybackEndpoint:
    """通过真实 HTTP/WebSocket 协议连接 server 的 Python playback endpoint。

    主要功能：模拟一台端侧设备，完成注册、唤醒、`sensor.mic` 上传、
    `actuator.speaker` 下行接收和播放回执。
    主要方法：`run_once()`。
    """

    def __init__(
        self,
        *,
        server_url: str,
        user_id: str,
        device_id: str,
        runs_root: str = "runs/audio-chat",
        auth: dict[str, Any] | None = None,
        device_name: str = "python-playback",
        client_type: str = "python-playback",
        properties: dict[str, Any] | None = None,
        supports: dict[str, Any] | None = None,
        subscriptions: list[dict[str, Any]] | None = None,
        rgb_payload: bytes | None = None,
        chunk_interval_ms: int = 0,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.user_id = user_id
        self.device_id = device_id
        self.runs_root = Path(runs_root)
        self.auth = auth or {"mode": "disabled"}
        self.device_name = device_name
        self.client_type = client_type
        self.properties = properties or {
            "audio.wake_word": "endpoint",
            "audio.aec": "endpoint",
        }
        self.supports = supports or {
            "sensors": [
                {"type": "rgb", "modes": ["single"], "default": {"format": "jpeg", "frequency_hz": 1, "sample_count": 1}}
            ]
        }
        self.subscriptions = subscriptions or [
            {"event": "control.audio_session.*"},
            {"event": "stream.output.*", "filter": {"stream_type": "actuator.speaker"}},
            {"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}},
        ]
        self.rgb_payload = rgb_payload or b"\xff\xd8mock-rgb-network\xff\xd9"
        self.chunk_interval_ms = chunk_interval_ms
        self.sent_events: list[Event] = []
        self.received_events: list[Event] = []
        self.output_chunks: list[StreamChunk] = []
        self.asset_uploads: list[dict[str, Any]] = []
        self._started_output_streams: set[str] = set()
        self._session_id: str | None = None
        self._input_stream_id: str | None = None
        self._output_closed = asyncio.Event()
        self._session_closed = asyncio.Event()
        self._registered = asyncio.Event()

    async def run_until_registered(self, *, session: ClientSession | None = None):
        """建立控制连接并完成注册，返回控制 WebSocket。

        功能：
        1. 供多设备 mock 先注册不同能力设备。
        2. 复用真实 `/ws/control` 路径，不走 server 内部 mock。

        参数：
        1. `session`：可选 aiohttp ClientSession；为空时由调用方不应使用本方法。

        返回值：
        1. 已完成注册的控制 WebSocket。

        异常情况：
        1. server 未启动、注册失败或协议响应异常时抛出 RuntimeError。
        """

        if session is None:
            raise ValueError("run_until_registered requires an existing ClientSession")
        control_ws = await session.ws_connect(self._control_url())
        await self._send_event(control_ws, self._registration_event())
        registered = await self._receive_event(control_ws)
        self.received_events.append(registered)
        if registered.event_name != "control.device.registered":
            await control_ws.close()
            raise RuntimeError(f"device registration failed: {registered.payload}")
        await self._send_event(
            control_ws,
            Event(
                event_name="control.device.heartbeat.received",
                user_id=self.user_id,
                producer_id=self.device_id,
                payload={"connection_id": registered.payload.get("connection_id")},
            ),
        )
        self._registered.set()
        return control_ws

    async def run_once(self, audio_payload: bytes | None = None, *, audio: PlaybackAudio | None = None) -> dict[str, Any]:
        """执行一次网络 playback 闭环。

        主要逻辑：建立控制和 stream WebSocket，注册设备，唤醒后按 server 请求打开
        `sensor.mic`，收到 speaker chunk 后上报 started / finished / closed。
        参数：`audio_payload` 为可选 PCM 输入，`audio` 为可选 WAV 分片输入。
        返回值：包含事件链、输出字节数和断言结果的 dict。
        异常情况：server 未启动、协议错误或断言超时会抛出异常。
        """
        playback_audio = audio or _pcm_audio(audio_payload)
        async with ClientSession() as session:
            control_ws = await self.run_until_registered(session=session)
            try:
                async with session.ws_connect(self._stream_url()) as stream_ws:
                    control_task = asyncio.create_task(self._control_loop(control_ws, stream_ws, playback_audio))
                    stream_task = asyncio.create_task(self._stream_loop(control_ws, stream_ws))
                    await self._send_event(
                        control_ws,
                        Event(
                            event_name="control.user.wake.detected",
                            user_id=self.user_id,
                            producer_id=self.device_id,
                            payload={"wake_source": "playback"},
                        ),
                    )
                    await asyncio.wait_for(self._output_closed.wait(), timeout=10)
                    await self._send_event(
                        control_ws,
                        Event(
                            event_name="control.user.dialog.close.requested",
                            user_id=self.user_id,
                            producer_id=self.device_id,
                            session_id=self._session_id,
                            payload={"reason": "playback_done"},
                        ),
                    )
                    await asyncio.wait_for(self._session_closed.wait(), timeout=10)
                    control_task.cancel()
                    stream_task.cancel()
            finally:
                await control_ws.close()
        result = self._build_result()
        result["input_audio"] = _audio_result(playback_audio)
        if self._session_id:
            session_dir = self._session_artifact_dir()
            (session_dir / "playback-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            (session_dir / "result.json").write_text(
                json.dumps({"ok": result["passed"], "status": "ok" if result["passed"] else "failed", **result}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._mirror_legacy_session_artifacts(session_dir)
        return result

    async def _control_loop(self, control_ws, stream_ws, audio: PlaybackAudio) -> None:
        async for message in control_ws:
            if message.type != WSMsgType.TEXT:
                continue
            event = Event.from_dict(json.loads(message.data))
            self.received_events.append(event)
            if event.event_name == "control.audio_session.open.requested":
                self._session_id = event.session_id
                await self._send_event(
                    control_ws,
                    Event(
                        event_name="control.audio_session.opened",
                        user_id=self.user_id,
                        producer_id=self.device_id,
                        session_id=self.device_id,
                        payload={"reason": "playback_opened"},
                    ),
                )
                await self._open_and_send_mic(control_ws, stream_ws, event.session_id, audio)
            elif event.event_name == "stream.output.close.requested":
                await self._send_event(
                    control_ws,
                    Event(
                        event_name="stream.output.finished",
                        user_id=self.user_id,
                        producer_id=self.device_id,
                        session_id=self.device_id,
                        stream_id=event.stream_id,
                        stream_type=event.stream_type,
                        payload={"stream_type": event.stream_type},
                    ),
                )
                await self._send_event(
                    control_ws,
                    Event(
                        event_name="stream.output.closed",
                        user_id=self.user_id,
                        producer_id=self.device_id,
                        session_id=self.device_id,
                        stream_id=event.stream_id,
                        stream_type=event.stream_type,
                        payload={"stream_type": event.stream_type, "reason": "playback_closed"},
                    ),
                )
                self._output_closed.set()
            elif event.event_name == "control.audio_session.close.requested":
                await self._send_event(
                    control_ws,
                    Event(
                        event_name="control.audio_session.closed",
                        user_id=self.user_id,
                        producer_id=self.device_id,
                        session_id=self.device_id,
                        payload={"reason": "playback_closed"},
                    ),
                )
                self._session_closed.set()
            elif event.event_name == "stream.control.open.requested" and event.stream_type == "sensor.rgb":
                await self._open_and_send_rgb_asset(control_ws, stream_ws, event)

    async def _stream_loop(self, control_ws, stream_ws) -> None:
        async for message in stream_ws:
            if message.type != WSMsgType.BINARY:
                continue
            chunk = StreamChunkCodec.decode(message.data)
            if chunk.stream_id not in self._started_output_streams:
                self._started_output_streams.add(chunk.stream_id)
                await self._send_event(
                    control_ws,
                    Event(
                        event_name="stream.output.started",
                        user_id=self.user_id,
                        producer_id=self.device_id,
                        session_id=self.device_id,
                        stream_id=chunk.stream_id,
                        stream_type=chunk.stream_type,
                        payload={"stream_type": chunk.stream_type},
                    ),
                )
            self.output_chunks.append(chunk)

    async def _open_and_send_mic(self, control_ws, stream_ws, session_id: str | None, audio: PlaybackAudio) -> None:
        stream_id = new_id("stream_in")
        self._input_stream_id = stream_id
        stream_format = audio.format
        await self._send_event(
            control_ws,
            Event(
                event_name="stream.input.opened",
                user_id=self.user_id,
                producer_id=self.device_id,
                session_id=session_id,
                stream_id=stream_id,
                stream_type="sensor.mic",
                payload={"stream_type": "sensor.mic", "format": stream_format.__dict__},
            ),
        )
        for seq, payload in enumerate(audio.chunks):
            await stream_ws.send_bytes(
                StreamChunkCodec.encode(
                    StreamChunk(
                        user_id=self.user_id,
                        session_id=session_id or "",
                        stream_id=stream_id,
                        stream_type="sensor.mic",
                        seq=seq,
                        payload=payload,
                        final=seq == len(audio.chunks) - 1,
                        codec=stream_format.codec,
                        sample_rate=stream_format.sample_rate,
                        channels=stream_format.channels,
                        duration_ms=stream_format.chunk_ms,
                        metadata={"source_path": audio.source_path} if audio.source_path else {},
                    )
                )
            )
            if self.chunk_interval_ms > 0 and seq < len(audio.chunks) - 1:
                await asyncio.sleep(self.chunk_interval_ms / 1000)
        # 控制和 stream 使用两条 WebSocket，端侧先给 server 一个极短处理窗口，避免
        # `stream.input.closed` 在二进制 chunk 前到达导致 server 提前关闭输入流。
        await asyncio.sleep(0.05)
        await self._send_event(
            control_ws,
            Event(
                event_name="stream.input.closed",
                user_id=self.user_id,
                producer_id=self.device_id,
                session_id=session_id,
                stream_id=stream_id,
                stream_type="sensor.mic",
                payload={"stream_type": "sensor.mic", "reason": "playback_input_done"},
            ),
        )

    async def _open_and_send_rgb_asset(self, control_ws, stream_ws, event: Event) -> None:
        """按控制事件上传一帧 `sensor.rgb` 资产。

        主要逻辑：端侧收到 `stream.control.open.requested` 后，使用真实 stream
        WebSocket 打开 `sensor.rgb` 输入流并携带 request_id 回传 JPEG bytes，避免把图片
        放进控制事件 payload。
        参数：`control_ws` 和 `stream_ws` 是当前端侧网络连接，`event` 是配置请求。
        返回值：无。
        异常情况：WebSocket 写入失败时向上传递异常。
        """

        request_id = str(event.payload.get("request_id") or "")
        stream_id = new_id("stream_rgb")
        session_id = event.session_id or self._session_id or self.device_id
        metadata = {"request_id": request_id} if request_id else {}
        await self._send_event(
            control_ws,
            Event(
                event_name="stream.input.opened",
                user_id=self.user_id,
                producer_id=self.device_id,
                session_id=session_id,
                stream_id=stream_id,
                stream_type="sensor.rgb",
                payload={
                    "stream_type": "sensor.rgb",
                    "format": {"codec": "jpeg", "sample_rate": 1, "channels": 1, "chunk_ms": 1},
                },
            ),
        )
        await stream_ws.send_bytes(
            StreamChunkCodec.encode(
                StreamChunk(
                    user_id=self.user_id,
                    session_id=session_id,
                    stream_id=stream_id,
                    stream_type="sensor.rgb",
                    seq=0,
                    payload=self.rgb_payload,
                    codec="jpeg",
                    sample_rate=1,
                    channels=1,
                    duration_ms=1,
                    final=True,
                    metadata=metadata,
                )
            )
        )
        await asyncio.sleep(0.05)
        await self._send_event(
            control_ws,
            Event(
                event_name="stream.input.closed",
                user_id=self.user_id,
                producer_id=self.device_id,
                session_id=session_id,
                stream_id=stream_id,
                stream_type="sensor.rgb",
                payload={"stream_type": "sensor.rgb", "reason": "asset_uploaded"},
            ),
        )
        self.asset_uploads.append({"stream_id": stream_id, "request_id": request_id, "payload_size": len(self.rgb_payload)})

    async def _send_event(self, ws, event: Event) -> None:
        self.sent_events.append(event)
        await ws.send_str(json.dumps(event.to_dict(), ensure_ascii=False))

    async def _receive_event(self, ws) -> Event:
        message = await ws.receive(timeout=5)
        if message.type != WSMsgType.TEXT:
            raise RuntimeError(f"unexpected websocket message type: {message.type}")
        return Event.from_dict(json.loads(message.data))

    def _registration_event(self) -> Event:
        return Event(
            event_name="control.device.register.requested",
            user_id=self.user_id,
            producer_id=self.device_id,
            payload={
                "device_id": self.device_id,
                "name": self.device_name,
                "device_name": self.device_name,
                "client_type": self.client_type,
                "sdk_version": "audio-chat-endpoint-0.1.0",
                "auth": self.auth,
                "properties": self.properties,
                "supports": self.supports,
                "subscriptions": self.subscriptions,
            },
        )

    def _control_url(self) -> str:
        return self.server_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws/control"

    def _stream_url(self) -> str:
        return (
            self.server_url.replace("http://", "ws://").replace("https://", "wss://")
            + f"/ws/stream?device_id={self.device_id}"
        )

    def _build_result(self) -> dict[str, Any]:
        event_names = [event.event_name for event in self.received_events + self.sent_events]
        if self._session_id:
            session_events_path = self._session_artifact_dir() / "events.jsonl"
            if session_events_path.exists():
                for line in session_events_path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        event_name = json.loads(line)["event_name"]
                        if event_name not in event_names:
                            event_names.append(event_name)
        result = {
            "user_id": self.user_id,
            "device_id": self.device_id,
            "session_id": self._session_id,
            "event_names": event_names,
            "endpoint_received_event_names": [event.event_name for event in self.received_events],
            "endpoint_sent_event_names": [event.event_name for event in self.sent_events],
            "output_chunk_count": len(self.output_chunks),
            "output_bytes": sum(len(chunk.payload) for chunk in self.output_chunks),
            "asset_uploads": list(self.asset_uploads),
            "supports": self.supports,
            "subscriptions": list(self.subscriptions),
            "transport": "network",
        }
        result["assertions"] = {event_name: event_name in result["event_names"] for event_name in PLAYBACK_REQUIRED_EVENTS}
        result["passed"] = all(result["assertions"].values()) and result["output_chunk_count"] > 0
        if self._session_id:
            session_dir = self._session_artifact_dir()
            session_dir.mkdir(parents=True, exist_ok=True)
            (session_dir / "playback-result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            result_record = {"ok": result["passed"], "status": "ok" if result["passed"] else "failed", **result}
            (session_dir / "result.json").write_text(
                json.dumps(result_record, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._mirror_legacy_session_artifacts(session_dir)
        return result

    def _session_artifact_dir(self) -> Path:
        """返回当前网络 playback 的运行产物目录。

        主要逻辑：新版 RunRecorder 按 `runs_root/<user_id>/<device_id>` 保存产物；
        旧测试和旧脚本可能仍读取 `runs_root/sessions/<session_id>`。本方法优先使用
        新目录，找不到时回退旧目录，避免网络端测误判真实 text 路线失败。
        返回值：会话产物目录。
        异常情况：`_session_id` 为空时返回旧目录占位。
        """

        if not self._session_id:
            return self.runs_root / "sessions" / "unknown"
        candidates = [
            self.runs_root / self.user_id / self._session_id,
            self.runs_root / "sessions" / self._session_id,
            self.runs_root / "_unbound" / self._session_id,
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        candidates[0].mkdir(parents=True, exist_ok=True)
        return candidates[0]

    def _mirror_legacy_session_artifacts(self, source_dir: Path) -> None:
        """把新版运行产物镜像到旧 `sessions/<id>` 目录。

        主要逻辑：只复制文件，不移动媒体目录。这样新版目录仍是主入口，旧的无头回放
        脚本也能在迁移期读取关键 JSONL/JSON 产物。
        参数：`source_dir` 为新版会话目录。
        返回值：无。
        异常情况：文件复制失败时向上传递，测试应暴露真实问题。
        """

        if not self._session_id:
            return
        legacy_dir = self.runs_root / "sessions" / self._session_id
        if legacy_dir.resolve() == source_dir.resolve():
            return
        legacy_dir.mkdir(parents=True, exist_ok=True)
        for path in source_dir.iterdir():
            if path.is_file():
                shutil.copy2(path, legacy_dir / path.name)
            elif path.is_dir():
                for child in path.rglob("*"):
                    if child.is_file():
                        target_dir = legacy_dir / child.relative_to(source_dir).parent
                        target_dir.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(child, target_dir / child.name)
                        if path.name == "audio":
                            shutil.copy2(child, legacy_dir / child.name)


def _coerce_package_names(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    """将配置中的包名归一化为元组。

    函数功能：兼容 YAML/JSON 中常见的字符串、列表和空值写法。
    主要逻辑：空值使用默认包名，单个字符串包装成单元素元组，列表或元组逐项转为字符串。
    参数：
        value：用户配置中的包名字段。
        default：没有配置时使用的默认包名。
    返回值：可传给 AppConfig 的包名元组。
    异常情况：本函数不主动抛出异常，异常值会被转换为字符串以便后续发现导入问题。
    """

    if value is None or value == "":
        return default
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable):
        return tuple(str(item) for item in value)
    return (str(value),)


def run_playback(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or {}
    app_root = str(config.get("app_root") or "").strip()
    if app_root:
        app_path = Path(app_root).resolve()
        if str(app_path) not in sys.path:
            sys.path.insert(0, str(app_path))
        for name in list(sys.modules):
            if name == "capabilities" or name.startswith("capabilities."):
                sys.modules.pop(name, None)
    discover_enabled = bool(config.get("discover_capabilities") or app_root)
    tools_discover_packages = _coerce_package_names(config.get("tools_discover_packages"), ("capabilities",))
    tasks_discover_packages = _coerce_package_names(config.get("tasks_discover_packages"), ("capabilities",))
    app = AudioChatApp(
        AudioChatConfig(
            runs_root=config.get("runs_root", "runs/audio-chat"),
            asr_provider=config.get("asr_provider", "mock"),
            asr_model=config.get("asr_model", "mock-asr"),
            text_model_provider=config.get("text_model_provider", "mock"),
            text_model=config.get("text_model", "mock-text"),
            tts_provider=config.get("tts_provider", "mock"),
            tts_model=config.get("tts_model", "mock-tts"),
            tts_voice=config.get("tts_voice", "mock"),
            asset_root=config.get("asset_root"),
            tools_discover_enabled=discover_enabled,
            tools_discover_packages=tools_discover_packages,
            tools_discover_recursive=bool(config.get("tools_discover_recursive", True)),
            tasks_discover_enabled=discover_enabled,
            tasks_discover_packages=tasks_discover_packages,
            tasks_discover_recursive=bool(config.get("tasks_discover_recursive", True)),
            mcp_enabled=bool(config.get("mcp_enabled", False)),
            mcp_config_path=str(config.get("mcp_config_path") or "mcp.json"),
            mcp_default_timeout_seconds=float(config.get("mcp_default_timeout_seconds") or 30),
        )
    )
    endpoint = PythonPlaybackEndpoint(
        app=app,
        user_id=config.get("user_id", "user-playback-001"),
        device_id=config.get("device_id", "dev-python-playback-001"),
        sensor_profiles=dict(config.get("sensor_profiles") or {}),
        heading=dict(config.get("heading") or {}),
        location=dict(config.get("location") or {}),
    )
    scenario = dict(config.get("scenario") or {})
    if scenario or config.get("actions"):
        if not scenario:
            scenario = {"actions": list(config.get("actions") or []), "assert": dict(config.get("assert") or {})}
        return endpoint.run_scripted(scenario)
    audio = _audio_from_action(config)
    return endpoint.run_once(audio=audio, chunk_interval_ms=int(config.get("chunk_interval_ms") or 0))


async def run_network_playback(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or {}
    endpoint = NetworkPythonPlaybackEndpoint(
        server_url=config.get("server_url", "http://127.0.0.1:8765"),
        user_id=config.get("user_id", "user-playback-001"),
        device_id=config.get("device_id", "dev-python-playback-001"),
        runs_root=config.get("runs_root", "runs/audio-chat"),
        auth=dict(config.get("auth") or {"mode": "disabled"}),
        properties=dict(config.get("properties") or {}) or None,
        supports=dict(config.get("supports") or {}) or None,
        subscriptions=list(config.get("subscriptions") or []) or None,
        chunk_interval_ms=int(config.get("chunk_interval_ms") or 0),
    )
    audio = _audio_from_action(config)
    return await endpoint.run_once(audio=audio)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="")
    parser.add_argument("--server-url", default="")
    parser.add_argument("--audio-wav", default="")
    parser.add_argument("--user-id", default="")
    parser.add_argument("--device-id", default="")
    parser.add_argument("--runs-root", default="")
    parser.add_argument("--chunk-interval-ms", type=int, default=None)
    parser.add_argument("--mode", choices=["network", "in_process"], default="")
    args = parser.parse_args(argv)
    config: dict[str, Any] = {}
    if args.config:
        text = Path(args.config).read_text(encoding="utf-8")
        if text.strip().startswith("{"):
            config = json.loads(text)
        else:
            import yaml

            config = yaml.safe_load(text) or {}
    if args.server_url:
        config["server_url"] = args.server_url
    if args.audio_wav:
        config["audio_wav"] = args.audio_wav
    if args.user_id:
        config["user_id"] = args.user_id
    if args.device_id:
        config["device_id"] = args.device_id
    if args.runs_root:
        config["runs_root"] = args.runs_root
    if args.chunk_interval_ms is not None:
        config["chunk_interval_ms"] = args.chunk_interval_ms
    if args.mode:
        config["mode"] = args.mode
    if config.get("mode") == "in_process":
        result = run_playback(config)
    else:
        result = asyncio.run(run_network_playback(config))
    if not result.get("passed", False):
        raise SystemExit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))
