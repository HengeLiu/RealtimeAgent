from __future__ import annotations

import asyncio
import json
from pathlib import Path

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.protocol import Event, StreamChunk, StreamFormat


class ForBlindAppPlaybackEndpoint:
    """测试用端侧回放端点。

    主要功能：模拟具备 `sensor.rgb` 上传和 `actuator.speaker` 消费能力的端侧设备。
    """

    def __init__(self, *, app: AudioChatApp, user_id: str, device_id: str) -> None:
        self.app = app
        self.user_id = user_id
        self.device_id = device_id
        self.events: list[Event] = []
        self.output_chunks: list[StreamChunk] = []

    def push_event(self, event: Event) -> None:
        """处理服务端下发事件。"""

        self.events.append(event)
        if event.event_name != "stream.control.open.requested" or event.stream_type != "sensor.rgb":
            return
        mode = event.payload.get("mode")
        if mode == "single":
            self._upload_rgb(request_id=event.payload.get("request_id"), correlation_id=None, count=1)
        elif mode == "continuous":
            self._upload_rgb(
                request_id=event.payload.get("request_id"),
                correlation_id=event.payload.get("correlation_id"),
                count=3,
            )

    def push_stream_chunk(self, chunk: StreamChunk) -> None:
        """记录服务端下发的 output stream chunk。"""

        self.output_chunks.append(chunk)

    def close(self, *, reason: str) -> None:
        """测试端点关闭钩子。"""

    def _upload_rgb(self, *, request_id: str | None, correlation_id: str | None, count: int) -> None:
        handle = self.app.open_input_stream(
            user_id=self.user_id,
            producer_id=self.device_id,
            stream_type="sensor.rgb",
            format=StreamFormat(codec="jpeg", sample_rate=1, channels=1, chunk_ms=1),
        )
        for seq in range(count):
            metadata = {}
            if request_id:
                metadata["request_id"] = request_id
            if correlation_id:
                metadata["correlation_id"] = correlation_id
            self.app.write_input_chunk(
                StreamChunk(
                    user_id=self.user_id,
                    session_id=handle.session_id,
                    stream_id=handle.stream_id,
                    stream_type="sensor.rgb",
                    seq=seq,
                    payload=b"\xff\xd8for-blind-app-frame-%d\xff\xd9" % seq,
                    codec="jpeg",
                    sample_rate=1,
                    channels=1,
                    duration_ms=1,
                    final=seq == count - 1,
                    metadata=metadata,
                )
            )
        self.app.stream_service.close_stream(handle.stream_id, reason="fixture_upload_done")


def register_for_blind_endpoint(app: AudioChatApp, endpoint: ForBlindAppPlaybackEndpoint) -> None:
    response = app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id=endpoint.user_id,
            producer_id=endpoint.device_id,
            payload={
                "device_id": endpoint.device_id,
                "device_name": endpoint.device_id,
                "client_type": "for-blind-app-playback",
                "sdk_version": "audio-chat-endpoint-0.1.0",
                "auth": {"mode": "disabled"},
                "supports": {"sensors": [{"type": "rgb"}], "actuators": []},
            },
        ),
        endpoint,
    )
    assert response.event_name == "control.device.registered"


def build_for_blind_app(tmp_path, monkeypatch) -> AudioChatApp:
    fixture_root = Path(__file__).resolve().parents[1] / "fixtures" / "for_blind_app"
    for name in list(__import__("sys").modules):
        if name == "capabilities" or name.startswith("capabilities."):
            __import__("sys").modules.pop(name, None)
    monkeypatch.syspath_prepend(str(fixture_root))
    return AudioChatApp(
        AudioChatConfig(
            runs_root=str(tmp_path / "runs"),
            asset_root=str(tmp_path / "runs" / "assets"),
            agent_mode="text",
            tools_discover_enabled=True,
            tools_discover_packages=("capabilities",),
            tools_discover_recursive=True,
            tasks_discover_enabled=True,
            tasks_discover_packages=("capabilities",),
            tasks_discover_recursive=True,
        )
    )


def test_for_blind_app_tool_and_task_playback_writes_explainable_artifacts(tmp_path, monkeypatch) -> None:
    """测试目标：验证“新增能力 -> 自动发现 -> 设备回放 -> 产物可检查”的闭环。

    测试方法：启动测试 for-blind app，注册回放端点，依次调用 `capture_photo` Tool
    和 `timer` Task。
    预期结果：能力无需修改 SDK 内部代码即可执行，runs 目录写入事件、stream、asset、
    tool、task、output 和 result 产物。
    """

    app = build_for_blind_app(tmp_path, monkeypatch)
    endpoint = ForBlindAppPlaybackEndpoint(app=app, user_id="user-for-blind", device_id="dev-for-blind")
    register_for_blind_endpoint(app, endpoint)
    session_id = app.active_session_id("user-for-blind")

    capture = asyncio.run(
        app.tool_gateway.call(
            name="capture_photo",
            user_id="user-for-blind",
            session_id=session_id,
            input_data={"reason": "acceptance"},
        )
    )
    timer_ref = asyncio.run(
        app.task_engine.create(
            task_type="timer",
            user_id="user-for-blind",
            session_id=session_id,
            input_data={"seconds": 1},
        )
    )
    result = {
        "ok": capture.ok and timer_ref.state == "running",
        "status": "ok",
        "tool": {"name": "capture_photo", "ok": capture.ok, "asset_count": len(capture.assets or [])},
        "tasks": [
            {"task_id": timer_ref.task_id, "task_type": timer_ref.task_type, "state": timer_ref.state},
        ],
        "endpoint_received_events": [event.event_name for event in endpoint.events],
        "output_chunk_count": len(endpoint.output_chunks),
        "asset_count": len(app.asset_service.query_assets(user_id="user-for-blind", stream_type="sensor.rgb")),
    }
    app.recorder.write_result(session_id, result)

    session_dir = app.recorder.session_dir(session_id, user_id="user-for-blind")
    required = [
        "events.jsonl",
        "stream-events.jsonl",
        "agent-events.jsonl",
        "tool-events.jsonl",
        "task-signals.jsonl",
        "assets.jsonl",
        "output-decisions.jsonl",
        "result.json",
    ]
    missing = [name for name in required if not (session_dir / name).exists()]

    assert capture.ok is True
    assert capture.assets
    assert result["asset_count"] >= 1
    assert result["output_chunk_count"] > 0
    assert "stream.control.open.requested" in result["endpoint_received_events"]
    assert missing == []

    tool_events = (session_dir / "tool-events.jsonl").read_text(encoding="utf-8")
    task_signals = (session_dir / "task-signals.jsonl").read_text(encoding="utf-8")
    assets = (session_dir / "assets.jsonl").read_text(encoding="utf-8")
    output_decisions = (session_dir / "output-decisions.jsonl").read_text(encoding="utf-8")
    final_result = json.loads((session_dir / "result.json").read_text(encoding="utf-8"))

    assert "capture_photo" in tool_events
    assert "timer.started" in task_signals
    assert "asset.stored" in assets
    assert "play_now" in output_decisions
    assert final_result["ok"] is True
