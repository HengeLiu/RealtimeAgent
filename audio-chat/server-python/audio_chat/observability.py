from __future__ import annotations

import json
import wave
from pathlib import Path
from typing import Any

from audio_chat.protocol import Event, StreamChunk


class RunRecorder:
    def __init__(self, runs_root: str | Path = "runs/audio-chat") -> None:
        self.runs_root = Path(runs_root)

    def session_dir(self, session_id: str) -> Path:
        path = self.runs_root / "sessions" / session_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def user_dir(self, user_id: str) -> Path:
        path = self.runs_root / "users" / user_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def record_event(self, event: Event) -> None:
        if event.session_id:
            self._append_jsonl(self.session_dir(event.session_id) / "events.jsonl", event.to_dict())
        self._append_jsonl(self.runs_root / "control-events.jsonl", event.to_dict())

    def record_stream_event(self, session_id: str, record: dict[str, Any]) -> None:
        self._append_jsonl(self.session_dir(session_id) / "stream-events.jsonl", record)

    def record_agent_event(self, session_id: str, record: dict[str, Any]) -> None:
        self._append_jsonl(self.session_dir(session_id) / "agent-events.jsonl", record)
        self._append_jsonl(self.session_dir(session_id) / "model-events.jsonl", record)

    def record_tool_trace(self, session_id: str, record: dict[str, Any]) -> None:
        """记录 Tool 调用轨迹。

        主要逻辑：写入稳定 `tool-trace.jsonl`，供回放和排障读取。
        参数：`session_id` 为会话，`record` 为工具调用结构。
        返回值：无。
        异常情况：文件写入失败时抛出 IO 异常。
        """
        self._append_jsonl(self.session_dir(session_id) / "tool-trace.jsonl", record)

    def record_task_event(self, session_id: str, record: dict[str, Any]) -> None:
        """记录 TaskEvent。

        主要逻辑：写入 `task-events.jsonl`。
        参数：`session_id` 为会话或任务标识，`record` 为任务事件结构。
        返回值：无。
        异常情况：文件写入失败时抛出 IO 异常。
        """
        self._append_jsonl(self.session_dir(session_id) / "task-events.jsonl", record)

    def record_model_request(self, session_id: str, record: dict[str, Any]) -> None:
        """记录模型请求。

        主要逻辑：写入 `model-request.json`，保留一轮交互发给模型的稳定请求快照。
        参数：`session_id` 为会话，`record` 为模型请求。
        返回值：无。
        异常情况：文件写入失败时抛出 IO 异常。
        """
        path = self.session_dir(session_id) / "model-request.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def write_result(self, session_id: str, record: dict[str, Any]) -> None:
        """写入会话结果。

        主要逻辑：输出稳定 `result.json`，作为回放断言入口。
        参数：`session_id` 为会话，`record` 为结果。
        返回值：无。
        异常情况：文件写入失败时抛出 IO 异常。
        """
        path = self.session_dir(session_id) / "result.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def record_playback_result(self, session_id: str, record: dict[str, Any]) -> None:
        path = self.session_dir(session_id) / "playback-result.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def record_system_event(self, record: dict[str, Any]) -> None:
        self._append_jsonl(self.runs_root / "system-events.jsonl", record)

    def record_playback_decision(self, session_id: str, record: dict[str, Any]) -> None:
        self._append_jsonl(self.session_dir(session_id) / "playback-decisions.jsonl", record)

    def write_playback_snapshot(self, record: dict[str, Any]) -> None:
        """写入播放仲裁调试快照。

        主要逻辑：把当前 active、queue 和最近决策写入固定文件，便于调试接口和回放对比读取。
        参数：`record` 为播放仲裁快照。
        返回值：无。
        异常情况：文件写入失败时抛出 IO 异常。
        """
        path = self.runs_root / "debug" / "playback.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def record_output_wav(
        self,
        *,
        session_id: str,
        stream_id: str,
        pcm: bytes,
        sample_rate: int,
        channels: int,
    ) -> None:
        """记录服务端下发的 PCM 输出音频。

        主要逻辑：把 actuator.speaker 的 pcm16le 载荷封装为 wav，作为回放和人工听检入口。
        参数：`session_id` 为会话，`stream_id` 为输出流，`pcm` 为原始音频字节。
        返回值：无。
        异常情况：文件写入失败时抛出 IO 异常。
        """
        path = self.session_dir(session_id) / f"output-{stream_id}.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(channels)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            handle.writeframes(pcm)

    def record_message(self, user_id: str, record: dict[str, Any]) -> None:
        self._append_jsonl(self.user_dir(user_id) / "messages.jsonl", record)

    def record_stream_payload(self, chunk: StreamChunk) -> None:
        name = "input" if chunk.stream_type.startswith("sensor.") else "output"
        path = self.session_dir(chunk.session_id) / f"{name}-{chunk.stream_id}.pcm"
        with path.open("ab") as handle:
            handle.write(chunk.payload)

    @staticmethod
    def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


class TurnRecorder:
    """单轮交互记录器。

    主要功能：吸收 RunRecorder 的写入能力，为回放提供输入流、转写、模型请求、
    Tool trace、TaskEvent、输出流和 result 的稳定入口。
    """

    def __init__(self, runs_root: str | Path = "runs/audio-chat") -> None:
        self.recorder = RunRecorder(runs_root)

    def record_input_stream(self, session_id: str, record: dict[str, Any]) -> None:
        self.recorder.record_stream_event(session_id, {"direction": "input", **record})

    def record_transcript(self, session_id: str, record: dict[str, Any]) -> None:
        self.recorder.record_agent_event(session_id, {"event": "transcript", **record})

    def record_model_request(self, session_id: str, record: dict[str, Any]) -> None:
        self.recorder.record_model_request(session_id, record)

    def record_agent_event(self, session_id: str, record: dict[str, Any]) -> None:
        self.recorder.record_agent_event(session_id, record)

    def record_tool_trace(self, session_id: str, record: dict[str, Any]) -> None:
        self.recorder.record_tool_trace(session_id, record)

    def record_task_event(self, session_id: str, record: dict[str, Any]) -> None:
        self.recorder.record_task_event(session_id, record)

    def record_output_stream(self, session_id: str, record: dict[str, Any]) -> None:
        self.recorder.record_stream_event(session_id, {"direction": "output", **record})

    def write_result(self, session_id: str, record: dict[str, Any]) -> None:
        self.recorder.write_result(session_id, record)
