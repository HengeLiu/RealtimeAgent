from __future__ import annotations

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.asset import ArtifactRef
from audio_chat.protocol import Event
from audio_chat.tasks import TaskEvent, TaskEventBridge


class SpeakerConnection:
    """测试用 speaker 端侧连接。"""

    def __init__(self) -> None:
        self.chunks = []

    def push_event(self, event: Event) -> None:
        """忽略控制事件。"""

    def push_stream_chunk(self, chunk) -> None:
        """记录输出音频 chunk。"""

        self.chunks.append(chunk)

    def close(self, *, reason: str) -> None:
        """关闭测试连接。"""


def register_speaker(app: AudioChatApp, connection: SpeakerConnection) -> None:
    """注册一个可消费 speaker output stream 的测试设备。"""

    app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id="user-bridge",
            producer_id="dev-speaker",
            payload={
                "device_id": "dev-speaker",
                "capabilities": {"streams.consume": ["actuator.speaker"], "actuator.speaker": True},
                "subscriptions": [{"event": "stream.output.*", "filter": {"stream_type": "actuator.speaker"}}],
                "auth": {"mode": "disabled"},
            },
        ),
        connection,
    )


def test_task_event_bridge_records_agent_sync_artifacts_and_direct_notify(tmp_path) -> None:
    """测试目标：验证 TaskEventBridge 同时覆盖上下文同步、artifact 和直接通知。

    测试方法：构造带 ArtifactRef、requires_agent_decision 和文本通知的 TaskEvent。
    预期结果：task-events、agent-events 和 output stream 都产生可检查产物。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    connection = SpeakerConnection()
    register_speaker(app, connection)
    bridge = TaskEventBridge(recorder=app.recorder, output_service=app.output_service)

    bridge.handle_event(
        TaskEvent(
            task_id="task-bridge",
            task_type="navigation",
            event_name="navigation.checkpoint",
            user_id="user-bridge",
            session_id="sess-bridge",
            payload={"text": "已到达路口"},
            requires_agent_decision=True,
            allow_direct_notify=True,
            artifacts=[ArtifactRef(artifact_id="artifact-1", kind="debug", uri="runs/debug.json")],
        )
    )

    task_events = (tmp_path / "runs" / "sessions" / "sess-bridge" / "task-events.jsonl").read_text(encoding="utf-8")
    agent_events = (tmp_path / "runs" / "sessions" / "sess-bridge" / "agent-events.jsonl").read_text(encoding="utf-8")

    assert "navigation.checkpoint" in task_events
    assert "artifact-1" in task_events
    assert "task.requires_agent_context_sync" in agent_events
    assert connection.chunks
