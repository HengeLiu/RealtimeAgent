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

    session_root = tmp_path / "runs" / "user-bridge" / "sess-bridge"
    task_events = (session_root / "task-events.jsonl").read_text(encoding="utf-8")
    agent_events = (session_root / "agent-events.jsonl").read_text(encoding="utf-8")

    assert "navigation.checkpoint" in task_events
    assert "artifact-1" in task_events
    assert "task.requires_agent_context_sync" in agent_events
    assert connection.chunks


def test_task_event_bridge_direct_notify_uses_complete_text_tts(tmp_path) -> None:
    """测试目标：验证 Task 到点通知使用完整文本 TTS 后再打开输出流。

    测试方法：注入一个只支持 `synthesize_text()` 的 TTS，模拟真实通知类输出不走
    assistant_text.delta 流式路径。
    预期结果：TaskEventBridge 处理 `timer.due` 后，speaker 连接收到音频 chunk，
    且 stream 摘要记录非空音频。
    """

    class CompleteTextTTS:
        """测试用完整文本 TTS。"""

        provider_name = "test-complete-text"
        model = "test-tts"
        streaming = False

        def __init__(self) -> None:
            self.texts: list[str] = []

        def synthesize_delta(self, text: str) -> bytes:
            """流式路径在本测试中不应被调用。"""

            raise AssertionError("direct notify should use synthesize_text")

        def synthesize_text(self, text: str) -> bytes:
            """把完整文本转换成固定 PCM，便于断言输出链路。"""

            self.texts.append(text)
            return b"\x01\x00" * 960

        def metrics(self) -> dict:
            """返回输出音频格式。"""

            return {"provider": self.provider_name, "model": self.model, "sample_rate_hz": 24000}

        def finish(self) -> None:
            """完整文本 TTS 不需要额外结束动作。"""

            return None

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    connection = SpeakerConnection()
    register_speaker(app, connection)
    fake_tts = CompleteTextTTS()
    app.output_service.router._injected_tts = fake_tts
    bridge = TaskEventBridge(recorder=app.recorder, output_service=app.output_service)

    bridge.handle_event(
        TaskEvent(
            task_id="task-timer",
            task_type="timer_task",
            event_name="timer.due",
            user_id="user-bridge",
            session_id="sess-timer",
            payload={"message": "一分钟计时器到点了"},
            priority="high",
            allow_direct_notify=True,
        )
    )

    assert fake_tts.texts == ["一分钟计时器到点了"]
    assert connection.chunks
    assert sum(len(chunk.payload) for chunk in connection.chunks) == 1920
    stream_events = (tmp_path / "runs" / "user-bridge" / "sess-timer" / "stream-events.jsonl").read_text(
        encoding="utf-8"
    )
    assert '"payload_size": 1920' in stream_events
