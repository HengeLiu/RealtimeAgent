from __future__ import annotations

import json

from realtime_agent.app import RealtimeAgentApp, RealtimeAgentConfig
from realtime_agent.output import AssistantTextDelta
from realtime_agent.output.service import OutputItem
from realtime_agent.protocol import Event, StreamChunk


class Connection:
    """测试连接，保存 output 控制事件和音频。"""

    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.events: list[Event] = []
        self.chunks: list[StreamChunk] = []

    def push_event(self, event: Event) -> None:
        """记录控制事件。"""

        self.events.append(event)

    def push_stream_chunk(self, chunk: StreamChunk) -> None:
        """记录音频 chunk。"""

        self.chunks.append(chunk)


def register_speaker(app: RealtimeAgentApp, connection: Connection) -> None:
    """注册可消费 speaker 的端侧。"""

    app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id="user-interrupt",
            producer_id=connection.device_id,
            payload={
                "device_id": connection.device_id,
                "auth": {"mode": "disabled"},
                "supports": {"sensors": [], "actuators": []},
                "properties": {"realtime_agent.audio_output": "actuator.speaker"},
            },
        ),
        connection,
    )


def test_interrupt_records_cancel_close_events_and_output_decision(tmp_path) -> None:
    """测试目标：验证播放中打断会产出 cancel/close 事件和 output decision。

    测试方法：先提交一条非 final 输出保持播放活跃，再发布 wake-word interrupt。
    预期结果：端侧收到 cancel.requested/cancelled，runs 中记录 cancel_current 决策。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    connection = Connection("dev-interrupt")
    register_speaker(app, connection)
    intent = OutputItem(user_id="user-interrupt", session_id="sess-interrupt", priority="normal")
    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-interrupt", session_id="sess-interrupt", text="正在播放", intent=intent)
    )

    app.publish_control_event(
        Event(
            event_name="control.user.interrupt.detected",
            user_id="user-interrupt",
            producer_id="dev-interrupt",
            session_id="sess-interrupt",
            payload={"reason": "wake_word_interrupt"},
        )
    )

    event_names = [event.event_name for event in connection.events]
    assert "stream.output.cancel.requested" in event_names
    assert "stream.output.cancelled" in event_names
    decisions = [
        json.loads(line)
        for line in app.recorder.session_file("interruptions", "playback-decisions.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(
        decision["action"] == "cancel_current" and decision["reason"] == "wake_word_interrupt"
        for decision in decisions
    )
