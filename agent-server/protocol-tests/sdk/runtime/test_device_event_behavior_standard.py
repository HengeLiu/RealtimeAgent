from __future__ import annotations

from realtime_agent import RealtimeAgentApp, RealtimeAgentConfig
from realtime_agent.protocol import Event, SERVER_PRODUCER_ID, StreamChunk, StreamFormat


class BrowserLikeEndpoint:
    """浏览器眼镜式测试端点。

    主要功能：模拟一台通过 control WebSocket 消费服务端事件、通过 stream WebSocket
    收发二进制数据的设备。
    主要属性：`events` 记录服务端下发的控制事件，`chunks` 记录服务端下发的输出分片，
    `closed_reasons` 记录连接被替换或断开的原因。
    """

    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.events: list[Event] = []
        self.chunks: list[StreamChunk] = []
        self.closed_reasons: list[str] = []

    def push_event(self, event: Event) -> None:
        """记录服务端下发的控制事件。"""

        self.events.append(event)

    def push_stream_chunk(self, chunk: StreamChunk) -> None:
        """记录服务端下发的 stream chunk。"""

        self.chunks.append(chunk)

    def close(self, *, reason: str) -> None:
        """记录连接关闭原因。"""

        self.closed_reasons.append(reason)

    def event_names(self) -> list[str]:
        """返回端点已经收到的事件名列表。"""

        return [event.event_name for event in self.events]


def _browser_like_registration(user_id: str, device_id: str) -> Event:
    """构造浏览器眼镜参考实现使用的结构化注册事件。

    主要逻辑：普通能力只声明 `sensor.rgb` 和 `actuator.vibrator`；系统音频主链路通过
    properties 声明 `sensor.mic` 与 `actuator.speaker`，避免把麦克风和扬声器混入
    普通 supports。
    参数：`user_id` 和 `device_id` 为设备身份。
    返回值：标准注册事件。
    异常情况：无。
    """

    return Event(
        event_name="control.device.register.requested",
        user_id=user_id,
        producer_id=device_id,
        payload={
            "device_id": device_id,
            "name": "浏览器调试设备",
            "device_name": "浏览器调试设备",
            "client_type": "browser",
            "sdk_version": "test",
            "runtime": {"platform": "browser", "language": "javascript"},
            "auth": {"mode": "disabled"},
            "supports": {
                "sensors": [
                    {
                        "type": "rgb",
                        "modes": ["single", "continuous"],
                        "default": {"format": "jpeg", "frequency_hz": 1, "sample_count": 1},
                    }
                ],
                "actuators": [{"type": "vibrator", "commands": ["vibrate"]}],
            },
            "properties": {
                "realtime_agent.audio_input": "sensor.mic",
                "realtime_agent.audio_output": "actuator.speaker",
            },
        },
    )


def _register_browser_like_device(
    app: RealtimeAgentApp,
    *,
    user_id: str = "user-browser-standard",
    device_id: str = "dev-browser-standard",
) -> BrowserLikeEndpoint:
    """注册浏览器眼镜式设备并断言注册成功。

    主要逻辑：测试只使用协议事件进入 Server SDK，不绕过 ControlService 的注册和路由
    编译逻辑。
    参数：`app` 为待测应用，`user_id/device_id` 为身份。
    返回值：记录型端点。
    异常情况：注册失败时由断言暴露。
    """

    endpoint = BrowserLikeEndpoint(device_id)
    response = app.register_device(_browser_like_registration(user_id, device_id), endpoint)
    assert response.event_name == "control.device.registered"
    assert response.payload["heartbeat_interval_seconds"] > 0
    return endpoint


def test_browser_like_registration_compiles_standard_event_routes(tmp_path) -> None:
    """测试目标：验证设备注册会把结构化能力编译成标准事件消费路由。

    测试方法：按 browser-glass 形状注册设备，随后发布 RGB 采集、扬声器输出和心跳事件。
    预期结果：设备能收到 `stream.control.*`、`stream.output.*` 事件；心跳更新 debug 快照。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    user_id = "user-register-standard"
    device_id = "dev-register-standard"
    endpoint = _register_browser_like_device(app, user_id=user_id, device_id=device_id)

    app.publish_control_event(
        Event(
            event_name="control.device.heartbeat.received",
            user_id=user_id,
            producer_id=device_id,
            payload={"connection_state": "online", "client_type": "browser-glass"},
        )
    )
    app.publish_control_event(
        Event(
            event_name="stream.control.open.requested",
            user_id=user_id,
            producer_id=SERVER_PRODUCER_ID,
            session_id=device_id,
            stream_id="stream-rgb-standard",
            stream_type="sensor.rgb",
            payload={"request_id": "req-rgb-standard", "mode": "single"},
        )
    )
    app.stream_service.open_stream(
        user_id=user_id,
        session_id=device_id,
        producer_id=SERVER_PRODUCER_ID,
        stream_id="stream-speaker-standard",
        stream_type="actuator.speaker",
        format=StreamFormat(codec="pcm16le", sample_rate=24000, channels=1, chunk_ms=40),
    )

    snapshot = app.control_service.build_device_snapshot(device_id)
    assert snapshot["connection_state"] == "online"
    assert snapshot["properties"]["realtime_agent.audio_input"] == "sensor.mic"
    assert "stream.control.open.requested" in endpoint.event_names()
    assert "stream.output.open.requested" in endpoint.event_names()


def test_realtime_dialog_opens_after_wake_and_endpoint_ack(tmp_path) -> None:
    """测试目标：验证实时音视频对话的打开动作以事件握手为准。

    测试方法：端侧发布 wake，server 先下发 `control.audio_session.open.requested`；
    端侧确认 opened 后，再按当前 server 入口上报 `sensor.mic` 输入流。
    预期结果：Agent session 只在 endpoint opened 后落盘，mic stream 在当前实现中注册后才能接收 chunk。
    """

    app = RealtimeAgentApp(
        RealtimeAgentConfig(
            runs_root=str(tmp_path / "runs"),
            default_sensor_mic=StreamFormat(codec="pcm16le", sample_rate=16000, channels=1, chunk_ms=20),
        )
    )
    user_id = "user-dialog-standard"
    device_id = "dev-dialog-standard"
    stream_id = "stream-mic-standard"
    endpoint = _register_browser_like_device(app, user_id=user_id, device_id=device_id)

    app.publish_control_event(
        Event(
            event_name="control.user.wake.detected",
            user_id=user_id,
            producer_id=device_id,
            payload={"wake_source": "browser_device_button"},
        )
    )

    assert "control.audio_session.open.requested" in endpoint.event_names()
    session_dir = tmp_path / "runs" / user_id / device_id
    assert not (session_dir / "agent-events.jsonl").exists()

    app.publish_control_event(
        Event(
            event_name="control.audio_session.opened",
            user_id=user_id,
            producer_id=device_id,
            session_id=device_id,
            payload={"reason": "browser_device_opened"},
        )
    )
    app.mark_stream_connection_opened(device_id)
    app.publish_control_event(
        Event(
            event_name="stream.input.opened",
            user_id=user_id,
            producer_id=device_id,
            session_id=device_id,
            stream_id=stream_id,
            stream_type="sensor.mic",
            payload={
                "stream_type": "sensor.mic",
                "format": {"codec": "pcm16le", "sample_rate": 16000, "channels": 1, "chunk_ms": 20},
            },
        )
    )
    app.write_input_chunk(
        StreamChunk(
            user_id=user_id,
            session_id=device_id,
            stream_id=stream_id,
            stream_type="sensor.mic",
            seq=0,
            payload=b"\x00\x00" * 320,
            codec="pcm16le",
            sample_rate=16000,
            channels=1,
            duration_ms=20,
            final=False,
        )
    )

    assert "session.opened" in (session_dir / "agent-events.jsonl").read_text(encoding="utf-8")
    assert app.stream_service.registry.get(stream_id).stream_type == "sensor.mic"


def test_device_consumes_server_events_with_acknowledgement_events(tmp_path) -> None:
    """测试目标：验证端侧消费服务端事件后必须用标准事件回执。

    测试方法：server 下发 RGB 采集请求、扬声器输出 stream 和低频 command；测试模拟端侧
    按去重后的目标行为回视觉输入、输出关闭和命令终态事件。
    预期结果：server 能接收资产输入、向端侧写出音频分片，并接收端侧标准回执。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    user_id = "user-consume-standard"
    device_id = "dev-consume-standard"
    endpoint = _register_browser_like_device(app, user_id=user_id, device_id=device_id)

    app.publish_control_event(
        Event(
            event_name="stream.control.open.requested",
            user_id=user_id,
            producer_id=SERVER_PRODUCER_ID,
            session_id=device_id,
            stream_id="stream-rgb-consume",
            stream_type="sensor.rgb",
            payload={
                "request_id": "req-rgb-consume",
                "mode": "continuous",
                "frequency_hz": 2,
            },
        )
    )
    app.publish_control_event(
        Event(
            event_name="stream.input.opened",
            user_id=user_id,
            producer_id=device_id,
            session_id=device_id,
            stream_id="stream-rgb-consume",
            stream_type="sensor.rgb",
            payload={
                "stream_type": "sensor.rgb",
                "format": {"codec": "jpeg", "sample_rate": 1, "channels": 1, "chunk_ms": 1},
                "request_id": "req-rgb-consume",
            },
        )
    )
    for seq in range(2):
        app.write_input_chunk(
            StreamChunk(
                user_id=user_id,
                session_id=device_id,
                stream_id="stream-rgb-consume",
                stream_type="sensor.rgb",
                seq=seq,
                payload=b"\xff\xd8standard-rgb\xff\xd9",
                codec="jpeg",
                sample_rate=1,
                channels=1,
                duration_ms=500,
                final=False,
                metadata={"request_id": "req-rgb-consume", "frequency_hz": 2},
            )
        )
    app.publish_control_event(
        Event(
            event_name="stream.control.close.requested",
            user_id=user_id,
            producer_id=SERVER_PRODUCER_ID,
            session_id=device_id,
            stream_id="stream-rgb-consume",
            stream_type="sensor.rgb",
            payload={"stream_type": "sensor.rgb", "reason": "visual_context_done"},
        )
    )
    app.publish_control_event(
        Event(
            event_name="stream.input.closed",
            user_id=user_id,
            producer_id=device_id,
            session_id=device_id,
            stream_id="stream-rgb-consume",
            stream_type="sensor.rgb",
            payload={"stream_type": "sensor.rgb", "reason": "server_close_requested", "request_id": "req-rgb-consume"},
        )
    )

    speaker = app.stream_service.open_stream(
        user_id=user_id,
        session_id=device_id,
        producer_id=SERVER_PRODUCER_ID,
        stream_id="stream-speaker-consume",
        stream_type="actuator.speaker",
        format=StreamFormat(codec="pcm16le", sample_rate=24000, channels=1, chunk_ms=40),
    )
    app.stream_service.write_chunk(
        StreamChunk(
            user_id=user_id,
            session_id=device_id,
            stream_id=speaker.stream_id,
            stream_type="actuator.speaker",
            seq=0,
            payload=b"\x01\x00" * 480,
            codec="pcm16le",
            sample_rate=24000,
            channels=1,
            duration_ms=40,
            final=True,
        )
    )
    app.stream_service.close_stream(speaker.stream_id, reason="assistant_audio_done")
    for event_name in ("stream.output.started", "stream.output.closed"):
        app.publish_control_event(
            Event(
                event_name=event_name,
                user_id=user_id,
                producer_id=device_id,
                session_id=device_id,
                stream_id=speaker.stream_id,
                stream_type="actuator.speaker",
                payload={"stream_type": "actuator.speaker", "reason": "browser_device_ack"},
            )
        )
    app.publish_control_event(
        Event(
            event_name="command.requested",
            user_id=user_id,
            producer_id=SERVER_PRODUCER_ID,
            session_id=device_id,
            payload={"command": "haptic.vibrate", "request_id": "cmd-haptic-standard", "duration_ms": 120},
        )
    )
    app.publish_control_event(
        Event(
            event_name="command.completed",
            user_id=user_id,
            producer_id=device_id,
            session_id=device_id,
            payload={"command": "haptic.vibrate", "request_id": "cmd-haptic-standard"},
        )
    )

    assert "stream.control.open.requested" in endpoint.event_names()
    assert "stream.output.open.requested" in endpoint.event_names()
    assert any(
        name in endpoint.event_names()
        for name in ("stream.output.close.requested", "stream.output.finish.requested")
    )
    assert "command.requested" in endpoint.event_names()
    assert [chunk.stream_type for chunk in endpoint.chunks] == ["actuator.speaker"]
    assert app.asset_service.query_assets(user_id=user_id, stream_type="sensor.rgb")
    assert app.stream_service.registry.get(speaker.stream_id).state == "closed"
