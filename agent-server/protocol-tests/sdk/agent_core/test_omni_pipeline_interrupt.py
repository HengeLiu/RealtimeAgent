from __future__ import annotations

from realtime_agent.realtime_pipeline.omni import OmniInputBoundary


class _FakeCore:
    """OmniInputBoundary 测试用最小 core。

    主要功能：提供 `_record_provider_event`、状态字典和音频 stream 字典，避免测试连接真实
    realtime provider。
    """

    def __init__(self, *, state: str = "listening") -> None:
        self._state_by_session = {"session-001": state}
        self._audio_stream_by_session = {"session-001": "stream-mic-001"}
        self.provider_records: list[dict] = []

    def _record_provider_event(self, *, user_id: str, session_id: str, record: dict) -> None:
        self.provider_records.append({"user_id": user_id, "session_id": session_id, "record": record})


class _FakeEmitter:
    """OmniInputBoundary 测试用事件收集器。"""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, event: str, **payload) -> dict:
        item = {"event": event, **payload}
        self.events.append(item)
        return item


class _FakeOutputController:
    """OmniInputBoundary 测试用输出状态查询器。"""

    def __init__(self, *, active_stream_id: str | None) -> None:
        self.active_stream_id = active_stream_id

    def active_output_stream_id(self, *, user_id: str, session_id: str) -> str | None:
        return self.active_stream_id


def _install_boundary(*, state: str = "listening", active_stream_id: str | None = None) -> tuple[_FakeCore, _FakeEmitter]:
    core = _FakeCore(state=state)
    emitter = _FakeEmitter()
    OmniInputBoundary(core=core, emitter=emitter, output_controller=_FakeOutputController(active_stream_id=active_stream_id))
    return core, emitter


def test_omni_provider_speech_started_requests_cancel_even_while_only_listening() -> None:
    """测试目标：确认 provider speech_started 总是进入统一取消路径。

    测试方法：构造 listening 状态且没有 active output 的 OmniInputBoundary，然后注入
    `omni.input_audio_buffer.speech_started`。
    预期结果：输出 speech_started 和 output_cancel_requested；诊断字段显示当前没有活跃输出，
    后续 interrupt 逻辑可自行把无对象取消处理成 no-op。
    """

    core, emitter = _install_boundary(state="listening", active_stream_id=None)

    core._record_provider_event(
        user_id="user-001",
        session_id="session-001",
        record={"event": "omni.input_audio_buffer.speech_started"},
    )

    assert [event["event"] for event in emitter.events] == ["speech_started", "output_cancel_requested"]
    assert emitter.events[0]["has_active_output"] is False
    assert emitter.events[0]["interruptible_state"] is False
    assert emitter.events[0]["interruptible"] is False
    assert emitter.events[0]["will_cancel"] is False
    assert emitter.events[1]["interruptible"] is False
    assert core.provider_records


def test_omni_provider_speech_started_cancels_active_output() -> None:
    """测试目标：确认播放中用户说话仍会触发打断。

    测试方法：构造存在 active output 的 OmniInputBoundary，然后注入 provider speech_started。
    预期结果：输出 speech_started 和 output_cancel_requested，取消事件指向当前 output stream。
    """

    core, emitter = _install_boundary(state="speaking", active_stream_id="stream-out-001")

    core._record_provider_event(
        user_id="user-001",
        session_id="session-001",
        record={"event": "omni.input_audio_buffer.speech_started"},
    )

    assert [event["event"] for event in emitter.events] == ["speech_started", "output_cancel_requested"]
    assert emitter.events[0]["has_active_output"] is True
    assert emitter.events[0]["interruptible_state"] is True
    assert emitter.events[0]["interruptible"] is True
    assert emitter.events[0]["will_cancel"] is True
    assert emitter.events[1]["stream_id"] == "stream-out-001"
    assert emitter.events[1]["has_active_output"] is True
