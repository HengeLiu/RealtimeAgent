from __future__ import annotations

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.protocol import Event, StreamChunk


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


def register_audio_device(app: AudioChatApp, connection: Connection, user_id: str = "user-dialog") -> None:
    """注册支持连续语音会话的测试端侧。"""

    app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id=user_id,
            producer_id=connection.device_id,
            payload={
                "device_id": connection.device_id,
                "auth": {"mode": "disabled"},
                "routes": [{"event": "control.audio_session.*"}],
            },
        ),
        connection,
    )


def test_turn_ignored_does_not_close_persistent_realtime_session(tmp_path) -> None:
    """测试目标：验证 turn ignored 只记录状态，不关闭 persistent realtime session。

    测试方法：打开 mock realtime 会话后发布 `control.audio_session.turn.ignored`。
    预期结果：Realtime session 仍存在，没有下发 close.requested。
    """

    app = AudioChatApp(
        AudioChatConfig(runs_root=str(tmp_path / "runs"), agent_mode="realtime", realtime_provider="mock")
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
    model_events = (tmp_path / "runs" / "sessions" / session_id / "model-events.jsonl").read_text(encoding="utf-8")
    assert "control.audio_session.turn.ignored" in model_events


def test_model_close_request_is_ignored_without_explicit_allow(tmp_path) -> None:
    """测试目标：验证模型误触发关闭不会释放连续会话。

    测试方法：打开会话后模拟 server/model 来源的 dialog close request。
    预期结果：不会下发 close.requested，runs 中记录 ignored。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
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
    model_events = (tmp_path / "runs" / "sessions" / session_id / "model-events.jsonl").read_text(encoding="utf-8")
    assert "control.audio_session.turn.ignored" in model_events
    assert "model_called_close" in model_events


def test_user_close_request_still_closes_session(tmp_path) -> None:
    """测试目标：验证真实用户关闭请求不被模型误调用保护拦截。

    测试方法：端侧发布 `control.user.dialog.close.requested`。
    预期结果：server 下发 `control.audio_session.close.requested`。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
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
