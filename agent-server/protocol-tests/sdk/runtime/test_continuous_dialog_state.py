from __future__ import annotations

from realtime_agent.app import RealtimeAgentApp, RealtimeAgentConfig
from realtime_agent.protocol import Event, StreamChunk


class FailingOpenProvider:
    """测试 provider：模拟模型 Realtime WebSocket 建连超时。"""

    def open(self, *, user_id: str, session_id: str, callbacks) -> None:
        """模拟 provider open 失败。

        测试目标：让 Server SDK 的会话打开入口进入失败分支。
        测试方法：固定抛出 TimeoutError。
        预期结果：上层把失败收敛为一次关闭请求，后续 mic chunk 不再重复刷屏。
        """

        raise TimeoutError("provider open timeout")

    def close(self, *, user_id: str, reason: str) -> None:
        """模拟释放 provider 资源。"""

    def append_audio(self, chunk: StreamChunk) -> None:
        """模拟追加音频，本测试不应调用。"""

    def append_image(self, image: bytes, *, user_id: str, session_id: str, metadata: dict | None = None) -> None:
        """模拟追加图片，本测试不应调用。"""

    def commit_input(self, *, user_id: str, session_id: str, reason: str) -> None:
        """模拟提交输入，本测试不应调用。"""

    def cancel(self, *, user_id: str, reason: str) -> None:
        """模拟取消响应，本测试不应调用。"""


class Connection:
    """测试连接，保存下发控制事件。"""

    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.events: list[Event] = []
        self.chunks: list[StreamChunk] = []

    def push_event(self, event: Event) -> None:
        """记录下发事件。"""

        self.events.append(event)

    def push_stream_chunk(self, chunk: StreamChunk) -> None:
        """记录下发音频。"""

        self.chunks.append(chunk)


def register_audio_device(app: RealtimeAgentApp, connection: Connection, user_id: str = "user-dialog") -> None:
    """注册支持连续语音会话的测试端侧。"""

    app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id=user_id,
            producer_id=connection.device_id,
            payload={
                "device_id": connection.device_id,
                "auth": {"mode": "disabled"},
                "supports": {"sensors": [], "actuators": []},
                "properties": {
                    "realtime_agent.audio_input": "sensor.mic",
                    "realtime_agent.audio_output": "actuator.speaker",
                },
            },
        ),
        connection,
    )


def test_provider_open_failure_closes_session_without_stream_error_storm(tmp_path) -> None:
    """测试目标：provider 建连失败时不会在启动阶段重复抛出大量异常。

    测试方法：注册音频设备后注入固定 open 失败的 provider，发布
    `control.audio_session.opened`，再写入一片 mic chunk。
    预期结果：控制事件处理不抛异常，只下发一次 close.requested；后续 mic chunk
    被 failed session 吸收，不再追加新的 system.error.raised。
    """

    app = RealtimeAgentApp(
        RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="realtime", omni_provider="mock")
    )
    app.agent_core.provider_factory = lambda config: FailingOpenProvider()
    connection = Connection("dev-dialog")
    register_audio_device(app, connection)
    session_id = app.active_session_id("user-dialog")
    stream_id = "stream-mic-open-failed"

    app.publish_control_event(
        Event(
            event_name="control.audio_session.opened",
            user_id="user-dialog",
            producer_id="dev-dialog",
            session_id=session_id,
            stream_id=stream_id,
            stream_type="sensor.mic",
            payload={"format": {"codec": "pcm16le", "sample_rate": 16000, "channels": 1, "chunk_ms": 20}},
        )
    )

    close_requests = [event for event in connection.events if event.event_name == "control.audio_session.close.requested"]
    system_events = app.recorder.runs_root / "system-events.jsonl"
    error_text_before_chunk = system_events.read_text(encoding="utf-8")
    assert len(close_requests) == 1
    assert close_requests[0].payload["reason"] == "agent_session_open_failed"
    assert error_text_before_chunk.count("system.error.raised") == 1

    app.write_input_chunk(
        StreamChunk(
            user_id="user-dialog",
            session_id=session_id,
            stream_id=stream_id,
            stream_type="sensor.mic",
            seq=0,
            payload=b"\x00" * 640,
        )
    )

    error_text_after_chunk = system_events.read_text(encoding="utf-8")
    assert error_text_after_chunk.count("system.error.raised") == error_text_before_chunk.count("system.error.raised")


def test_turn_ignored_does_not_close_persistent_realtime_session(tmp_path) -> None:
    """测试目标：验证 turn ignored 只记录状态，不关闭 persistent realtime session。

    测试方法：打开 mock realtime 会话后发布 `control.audio_session.turn.ignored`。
    预期结果：Realtime session 仍存在，没有下发 close.requested。
    """

    app = RealtimeAgentApp(
        RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="realtime", omni_provider="mock")
    )
    connection = Connection("dev-dialog")
    register_audio_device(app, connection)
    session_id = app.active_session_id("user-dialog")
    app.publish_control_event(
        Event(
            event_name="control.audio_session.opened",
            user_id="user-dialog",
            producer_id="dev-dialog",
            session_id=session_id,
            payload={},
        )
    )

    app.publish_control_event(
        Event(
            event_name="control.audio_session.turn.ignored",
            user_id="user-dialog",
            producer_id="dev-dialog",
            session_id=session_id,
            payload={"reason": "wake_noise"},
        )
    )

    assert "user-dialog" in app.agent_core._sessions
    assert not any(event.event_name == "control.audio_session.close.requested" for event in connection.events)
    model_events = app.recorder.session_file(session_id, "model-events.jsonl").read_text(encoding="utf-8")
    assert "control.audio_session.turn.ignored" in model_events


def test_model_close_request_is_ignored_without_explicit_allow(tmp_path) -> None:
    """测试目标：验证模型误触发关闭不会释放连续会话。

    测试方法：打开会话后模拟 server/model 来源的 dialog close request。
    预期结果：不会下发 close.requested，runs 中记录 ignored。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    connection = Connection("dev-dialog")
    register_audio_device(app, connection)
    session_id = app.active_session_id("user-dialog")
    app.publish_control_event(
        Event(
            event_name="control.audio_session.opened",
            user_id="user-dialog",
            producer_id="dev-dialog",
            session_id=session_id,
            payload={},
        )
    )

    app.publish_control_event(
        Event(
            event_name="control.user.dialog.close.requested",
            user_id="user-dialog",
            producer_id="server-main",
            session_id=session_id,
            payload={"source": "model", "reason": "model_called_close"},
        )
    )

    assert not any(event.event_name == "control.audio_session.close.requested" for event in connection.events)
    model_events = app.recorder.session_file(session_id, "model-events.jsonl").read_text(encoding="utf-8")
    assert "control.audio_session.turn.ignored" in model_events
    assert "model_called_close" in model_events


def test_user_close_request_still_closes_session(tmp_path) -> None:
    """测试目标：验证真实用户关闭请求不被模型误调用保护拦截。

    测试方法：端侧发布 `control.user.dialog.close.requested`。
    预期结果：server 下发 `control.audio_session.close.requested`。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    connection = Connection("dev-dialog")
    register_audio_device(app, connection)
    session_id = app.active_session_id("user-dialog")
    app.publish_control_event(
        Event(
            event_name="control.audio_session.opened",
            user_id="user-dialog",
            producer_id="dev-dialog",
            session_id=session_id,
            payload={},
        )
    )

    app.publish_control_event(
        Event(
            event_name="control.user.dialog.close.requested",
            user_id="user-dialog",
            producer_id="dev-dialog",
            session_id=session_id,
            payload={"reason": "user_requested", "mode": "close_now"},
        )
    )

    assert any(event.event_name == "control.audio_session.close.requested" for event in connection.events)
