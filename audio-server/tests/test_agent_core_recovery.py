from __future__ import annotations

from audio_chat.agent_core.base import AgentEventBuffer
from audio_chat.agent_core.recovery import DEFAULT_RECOVERABLE_ERROR_MESSAGE, record_agent_recovery_error


class Recorder:
    """测试用运行产物记录器。

    主要功能：收集 recovery helper 试图写入的 session、system 和 agent 事件。
    主要属性：`events`、`system_events`、`agent_events` 分别保存三类记录。
    """

    def __init__(self) -> None:
        self.events = []
        self.system_events = []
        self.agent_events = []

    def record_event(self, event) -> None:
        """记录控制事件对象。"""
        self.events.append(event)

    def record_system_event(self, record: dict) -> None:
        """记录系统事件字典。"""
        self.system_events.append(record)

    def record_agent_event(self, session_id: str, record: dict) -> None:
        """记录 Agent 事件字典。"""
        self.agent_events.append((session_id, record))


class ControlService:
    """测试用控制服务。

    主要功能：收集下发给端侧的控制事件。
    主要属性：`published` 保存已发布事件。
    """

    def __init__(self) -> None:
        self.published = []

    def publish(self, event) -> None:
        """记录下发事件。"""
        self.published.append(event)


def test_record_agent_recovery_error_writes_shared_error_surfaces() -> None:
    """测试目标：验证 Agent Core 共享恢复 helper 会写入统一错误面。

    测试方法：用假 recorder、event_buffer 和 control_service 调用 helper。
    预期结果：session 事件、system 事件、agent 事件、内存事件和端侧控制事件都使用
    `system.error.raised` / `response.failed` 语义，并带有可恢复兜底文案。
    """

    recorder = Recorder()
    event_buffer = AgentEventBuffer()
    control_service = ControlService()

    event = record_agent_recovery_error(
        recorder=recorder,
        event_buffer=event_buffer,
        control_service=control_service,
        user_id="user-recovery",
        session_id="dev-recovery",
        stream_id="stream-in",
        stream_type="sensor.mic",
        component="TextAgentCore",
        error=RuntimeError("provider failed"),
        agent_event="response.failed",
        record={"provider": "fake"},
    )

    assert event.event_name == "system.error.raised"
    assert event.payload["component"] == "TextAgentCore"
    assert event.payload["error_type"] == "RuntimeError"
    assert event.payload["recoverable"] is True
    assert event.payload["fallback_text"] == DEFAULT_RECOVERABLE_ERROR_MESSAGE
    assert recorder.events == [event]
    assert recorder.system_events[0]["event_name"] == "system.error.raised"
    assert recorder.agent_events[0][1]["event"] == "response.failed"
    assert event_buffer.events()[0].event == "response.failed"
    assert control_service.published == [event]
