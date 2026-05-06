from __future__ import annotations

import json
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

    def record_playback_result(self, session_id: str, record: dict[str, Any]) -> None:
        path = self.session_dir(session_id) / "playback-result.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def record_system_event(self, record: dict[str, Any]) -> None:
        self._append_jsonl(self.runs_root / "system-events.jsonl", record)

    def record_playback_decision(self, session_id: str, record: dict[str, Any]) -> None:
        self._append_jsonl(self.session_dir(session_id) / "playback-decisions.jsonl", record)

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
