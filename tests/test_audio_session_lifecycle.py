from __future__ import annotations

import json

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.output import AssistantTextDelta
from audio_chat.protocol import Event, StreamChunk


class Connection:
    """测试用设备连接。

    主要功能：收集服务端按订阅下发的控制事件和 stream chunk。
    主要属性：`events` 保存事件，`chunks` 保存音频输出。
    """

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

    def close(self, *, reason: str) -> None:
        """模拟连接关闭。"""

        self.events.append(
            Event(
                event_name="control.device.state.changed",
                user_id="user-a",
                producer_id="server-main",
                payload={"reason": reason},
            )
        )


def register_audio_endpoint(app: AudioChatApp, connection: Connection, *, user_id: str = "user-a") -> None:
    """注册一个同时支持 mic 和 speaker 的测试端侧。"""

    app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id=user_id,
            producer_id=connection.device_id,
            payload={
                "device_id": connection.device_id,
                "auth": {"mode": "disabled"},
                "supports": {"sensors": [], "actuators": []},
            },
        ),
        connection,
    )


def test_wake_requests_session_and_agent_opens_only_after_endpoint_opened(tmp_path) -> None:
    """测试目标：验证 wake 后先请求端侧打开音频会话，endpoint opened 后才打开 Agent。

    测试方法：发布 `control.user.wake.detected`，检查 agent 事件为空；再发布
    `control.audio_session.opened`。
    预期结果：端侧收到 open.requested，Agent session.opened 只在 opened 后落盘。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    connection = Connection("dev-audio")
    register_audio_endpoint(app, connection)

    app.publish_control_event(
        Event(
            event_name="control.user.wake.detected",
            user_id="user-a",
            producer_id="dev-audio",
            payload={"wake_word": "manual"},
        )
    )

    open_event = next(event for event in connection.events if event.event_name == "control.audio_session.open.requested")
    assert open_event.session_id == "dev-audio"
    assert app.active_session_id("user-a") == "dev-audio"
    session_dir = tmp_path / "runs" / "user-a" / open_event.session_id
    assert not (session_dir / "agent-events.jsonl").exists()

    app.publish_control_event(
        Event(
            event_name="control.audio_session.opened",
            user_id="user-a",
            producer_id="dev-audio",
            session_id=open_event.session_id,
            payload={"reason": "endpoint_ready"},
        )
    )

    assert "session.opened" in (session_dir / "agent-events.jsonl").read_text(encoding="utf-8")


def test_close_after_reply_waits_for_current_output_then_requests_close(tmp_path) -> None:
    """测试目标：验证 `close_after_reply` 等当前 output stream 结束后再请求关闭会话。

    测试方法：打开会话并提交一段非 final assistant 文本，使 output stream 保持 active；
    再请求 close_after_reply，最后发送 final 触发 output 完成。
    预期结果：请求时不立刻下发 close.requested；输出完成后才下发 close.requested。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    connection = Connection("dev-audio")
    register_audio_endpoint(app, connection)
    session_id = app.active_session_id("user-a")
    app.publish_control_event(
        Event(
            event_name="control.audio_session.opened",
            user_id="user-a",
            producer_id="dev-audio",
            session_id=session_id,
            payload={"reason": "test"},
        )
    )
    app.output_service.on_assistant_text_delta(
        AssistantTextDelta(user_id="user-a", session_id=session_id, text="正在回答", final=False)
    )

    app.close_audio_session("user-a", reason="user_requested", mode="close_after_reply")

    assert not any(
        event.event_name == "control.audio_session.close.requested" and not event.payload.get("deferred")
        for event in connection.events
    )

    app.output_service.on_assistant_text_delta(
        AssistantTextDelta(user_id="user-a", session_id=session_id, text="", final=True)
    )

    assert any(
        event.event_name == "control.audio_session.close.requested"
        and event.payload.get("close_mode") == "close_after_reply"
        for event in connection.events
    )


def test_endpoint_closed_releases_dialog_but_keeps_device_identity(tmp_path) -> None:
    """测试目标：验证 endpoint 确认关闭后释放对话状态但不再生成新 session。

    测试方法：打开会话后请求 close_now，再模拟 endpoint 回 `control.audio_session.closed`。
    预期结果：closed 之前 active device 仍存在；closed 之后重新取链路标识仍是同一 device_id。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    connection = Connection("dev-audio")
    register_audio_endpoint(app, connection)
    session_id = app.active_session_id("user-a")
    app.publish_control_event(
        Event(
            event_name="control.audio_session.opened",
            user_id="user-a",
            producer_id="dev-audio",
            session_id=session_id,
            payload={},
        )
    )

    app.close_audio_session("user-a", reason="done", mode="close_now")
    assert app.active_session_id("user-a") == session_id

    app.publish_control_event(
        Event(
            event_name="control.audio_session.closed",
            user_id="user-a",
            producer_id="dev-audio",
            session_id=session_id,
            payload={"reason": "endpoint_closed"},
        )
    )

    assert app.active_session_id("user-a") == session_id


def test_endpoint_closed_compacts_messages_after_dialog(tmp_path) -> None:
    """测试目标：验证连续对话结束后自动压缩过长 active messages。

    测试方法：配置较低压缩阈值，写入 7 条消息后模拟端侧关闭音频会话。
    预期结果：active/messages 只保留最新 2 条，旧消息进入 history，并产生 summary 事件。
    """

    app = AudioChatApp(
        AudioChatConfig(
            runs_root=str(tmp_path / "runs"),
            message_compact_threshold=6,
            message_compact_keep_latest=2,
        )
    )
    connection = Connection("dev-audio")
    register_audio_endpoint(app, connection)
    session_id = app.active_session_id("user-a")
    app.publish_control_event(
        Event(
            event_name="control.audio_session.opened",
            user_id="user-a",
            producer_id="dev-audio",
            session_id=session_id,
            payload={},
        )
    )
    for index in range(7):
        app.control_service.append_message(
            "user-a",
            {
                "session_id": session_id,
                "role": "user" if index % 2 == 0 else "assistant",
                "content": f"会话消息 {index}",
                "created_at": 1_700_000_000 + index,
            },
        )

    app.publish_control_event(
        Event(
            event_name="control.audio_session.closed",
            user_id="user-a",
            producer_id="dev-audio",
            session_id=session_id,
            payload={"reason": "endpoint_closed"},
        )
    )

    session_dir = tmp_path / "runs" / "user-a" / session_id
    active = [json.loads(line) for line in (session_dir / "active-messages.jsonl").read_text(encoding="utf-8").splitlines()]
    legacy = [json.loads(line) for line in (session_dir / "messages.jsonl").read_text(encoding="utf-8").splitlines()]
    summary_text = (session_dir / "message-summaries.jsonl").read_text(encoding="utf-8")
    events_text = (session_dir / "agent-events.jsonl").read_text(encoding="utf-8")
    history_files = list((session_dir / "history").glob("*-messages.jsonl"))

    assert [item["content"] for item in active] == ["会话消息 5", "会话消息 6"]
    assert active == legacy
    assert len(history_files) == 1
    assert len(history_files[0].read_text(encoding="utf-8").splitlines()) == 5
    assert "会话消息 0" in summary_text
    assert "conversation.messages.compacted" in events_text


def test_maintenance_sweeper_expires_heartbeat_idle_stream_and_max_duration(tmp_path) -> None:
    """测试目标：验证后台清理可以由测试手动触发。

    测试方法：注册设备、打开空闲 stream 和超时音频会话，然后调用
    `run_maintenance_once()`。
    预期结果：设备心跳超时离线、stream idle 关闭、音频会话发起 close request。
    """

    app = AudioChatApp(
        AudioChatConfig(
            runs_root=str(tmp_path / "runs"),
            control_heartbeat_timeout_seconds=1,
            stream_idle_timeout_seconds=1,
            audio_session_max_duration_seconds=1,
        )
    )
    connection = Connection("dev-audio")
    register_audio_endpoint(app, connection)
    device_snapshot = app.control_service.build_device_snapshot("dev-audio")
    session_id = app.active_session_id("user-a")
    state = app._device_dialogs_by_user["user-a"]
    state.opened_at = device_snapshot["last_seen_at"] - 10
    handle = app.open_input_stream(user_id="user-a", producer_id="dev-audio")
    handle.last_activity_at = device_snapshot["last_seen_at"] - 10

    result = app.run_maintenance_once(now=device_snapshot["last_seen_at"] + 10)

    assert result["expired_devices"] == ["dev-audio"]
    assert handle.stream_id in result["closed_streams"]
    assert session_id in result["closed_audio_sessions"]
    assert app.control_service.build_device_snapshot("dev-audio")["connection_state"] == "offline"
