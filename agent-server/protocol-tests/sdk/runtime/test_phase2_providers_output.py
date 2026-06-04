import json
import wave

from realtime_agent.app import RealtimeAgentApp, RealtimeAgentConfig
from realtime_agent.output import AssistantTextDelta
from realtime_agent.output.service import NotificationRequest, OutputItem, MockStreamingTTS, OutputService, TtsProviderConfig
from realtime_agent.protocol import Event, StreamChunk, StreamFormat
from realtime_agent.tasks import TaskSignal, TaskSignalBridge


class Connection:
    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.events = []
        self.chunks = []
        self.app: RealtimeAgentApp | None = None

    def push_event(self, event: Event) -> None:
        self.events.append(event)
        if event.event_name == "stream.output.start.requested" and self.app is not None:
            self.app.publish_control_event(
                Event(
                    event_name="stream.output.ready",
                    user_id=event.user_id,
                    producer_id=self.device_id,
                    session_id=event.session_id,
                    stream_id=event.stream_id,
                    stream_type=event.stream_type,
                    payload={"stream_type": event.stream_type, "reason": "test_connection_ready"},
                )
            )

    def push_stream_chunk(self, chunk: StreamChunk) -> None:
        self.chunks.append(chunk)


class FinishPayloadTTS:
    """测试用 TTS：普通 delta 和 final flush 都返回可识别音频。"""

    provider_name = "finish-payload"
    model = "finish-payload"
    streaming = True

    def __init__(self) -> None:
        self.text_chars = 0

    def synthesize_delta(self, text: str) -> bytes:
        """按文本长度返回固定 PCM，模拟流式 TTS 首段音频。"""

        self.text_chars += len(text)
        return b"\x01\x00" * 480

    def synthesize_text(self, text: str) -> bytes:
        """返回完整文本播报音频，模拟 Task/Tool 直接通知。"""

        self.text_chars += len(text)
        return b"\x03\x00" * 480

    def drain_audio(self, wait_seconds: float = 0.0) -> bytes:
        """测试 TTS 没有后台音频。"""

        return b""

    def finish(self) -> bytes:
        """返回 final flush 音频，用于验证尾音没有被误删。"""

        return b"\x02\x00" * 480

    def cancel(self) -> None:
        """测试中不需要额外取消动作。"""

        return None

    def metrics(self) -> dict:
        """返回输出格式所需的最小指标。"""

        return {
            "provider": self.provider_name,
            "model": self.model,
            "sample_rate_hz": 24000,
            "text_chars": self.text_chars,
        }


def register_speaker(app: RealtimeAgentApp, connection: Connection, user_id: str = "user-001") -> None:
    connection.app = app
    app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id=user_id,
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


def session_text(app: RealtimeAgentApp, session_id: str, filename: str) -> str:
    """读取当前 recorder 绑定目录中的 session 文件。"""

    return app.recorder.session_file(session_id, filename).read_text(encoding="utf-8")


def close_output_stream(
    app: RealtimeAgentApp,
    *,
    user_id: str = "user-001",
    producer_id: str = "dev-playback",
    session_id: str,
    stream_id: str,
    reason: str = "test_endpoint_drain_done",
) -> None:
    """模拟端侧 speaker buffer 和本地播放器已经 drain 完成。"""

    app.publish_control_event(
        Event(
            event_name="stream.output.finished",
            user_id=user_id,
            producer_id=producer_id,
            session_id=session_id,
            stream_id=stream_id,
            stream_type="actuator.speaker",
            payload={"stream_type": "actuator.speaker", "reason": reason},
        )
    )


def test_vision_agent_streams_text_and_tts_audio_before_final_done(tmp_path) -> None:
    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    connection = Connection("dev-playback")
    register_speaker(app, connection)
    handle = app.open_input_stream(user_id="user-001", producer_id="dev-playback")

    app.write_input_chunk(
        StreamChunk(
            user_id="user-001",
            session_id=handle.session_id,
            stream_id=handle.stream_id,
            stream_type="sensor.mic",
            seq=0,
            payload=b"\x00\x00" * 320,
            final=True,
        )
    )

    model_events = session_text(app, handle.session_id, "model-events.jsonl")
    assert model_events.index("assistant_audio.delta") < model_events.index("assistant_audio.done")
    assert model_events.count("assistant_text.delta") >= 2
    assert len(connection.chunks) >= 2


def test_playback_arbiter_interrupts_lower_priority_stream(tmp_path) -> None:
    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    connection = Connection("dev-playback")
    register_speaker(app, connection)

    low = OutputItem(
        user_id="user-001",
        session_id="sess-output",
        priority="low",
        on_interrupted="drop",
        on_blocked="drop",
    )
    high = OutputItem(
        user_id="user-001",
        session_id="sess-output",
        priority="critical",
        on_interrupted="drop",
        on_blocked="queue",
    )

    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-output", text="low priority", intent=low)
    )
    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-output-2", text="critical priority", intent=high)
    )

    decisions = session_text(app, "sess-output", "playback-decisions.jsonl")
    assert "interrupt" in decisions
    assert any(event.event_name == "stream.output.cancel.requested" for event in connection.events)
    assert not any(event.event_name == "stream.output.cancelled" for event in connection.events)


def test_playback_arbiter_explicit_on_blocked_interrupt_cancels_equal_priority_stream(tmp_path) -> None:
    """测试目标：验证实时对话的新回答可以主动打断同优先级旧播放。

    测试方法：先提交一条 normal 输出占用播放链路，再提交另一条 normal 输出，
    但显式设置 `on_blocked="interrupt"`。
    预期结果：旧 stream 收到 cancel，新输出立即成为 active，不进入播放队列。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    connection = Connection("dev-playback")
    register_speaker(app, connection)
    active = OutputItem(user_id="user-001", session_id="sess-old", priority="normal")
    interrupting = OutputItem(
        user_id="user-001",
        session_id="sess-new",
        priority="normal",
        on_blocked="interrupt",
    )

    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-old", text="old output", intent=active)
    )
    old_stream_id = app.output_service.active_output_stream_id("user-001", "sess-old")
    assert old_stream_id is not None

    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-new", text="new output", intent=interrupting)
    )

    assert app.output_service.active_output_stream_id("user-001", "sess-new") is not None
    assert app.output_service.active_output_stream_id("user-001", "sess-old") is None
    assert any(
        event.event_name == "stream.output.cancel.requested" and event.stream_id == old_stream_id
        for event in connection.events
    )
    decisions = session_text(app, "sess-new", "playback-decisions.jsonl")
    assert "blocked_output_interrupt" in decisions
    assert "active_playback_not_preempted" not in decisions


def test_user_interrupt_cancels_current_output_stream(tmp_path) -> None:
    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    connection = Connection("dev-playback")
    register_speaker(app, connection)
    intent = OutputItem(user_id="user-001", session_id="sess-interrupt", priority="normal")

    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-interrupt", text="playing", intent=intent)
    )
    app.publish_control_event(
        Event(
            event_name="control.user.interrupt.detected",
            user_id="user-001",
            producer_id="dev-playback",
            session_id="sess-interrupt",
            payload={"reason": "test_interrupt"},
        )
    )

    event_names = [event.event_name for event in connection.events]
    assert "stream.output.cancel.requested" in event_names
    assert "stream.output.cancelled" not in event_names
    decisions = app.recorder.session_file("dev-playback", "playback-decisions.jsonl").read_text()
    assert "cancel_current" in decisions


def test_requeue_plays_after_interrupting_stream_finishes(tmp_path) -> None:
    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    connection = Connection("dev-playback")
    register_speaker(app, connection)
    low = OutputItem(
        user_id="user-001",
        session_id="sess-low",
        priority="low",
        on_interrupted="requeue",
        on_blocked="drop",
        ttl_seconds=30,
    )
    high = OutputItem(user_id="user-001", session_id="sess-high", priority="critical")

    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-low", text="low", intent=low)
    )
    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-high", text="high", intent=high)
    )
    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-high", text="", final=True, intent=high)
    )
    high_stream_id = app.output_service.active_output_stream_id("user-001", "sess-high")
    assert high_stream_id is not None
    close_output_stream(app, session_id="sess-high", stream_id=high_stream_id)

    low_decisions = session_text(app, "sess-low", "playback-decisions.jsonl")
    high_decisions = session_text(app, "sess-high", "playback-decisions.jsonl")
    assert "play_now" in low_decisions
    assert "interrupt" in high_decisions
    assert len([event for event in connection.events if event.event_name == "stream.output.start.requested"]) >= 3


def test_queue_ttl_expiry_and_same_priority_no_interrupt(tmp_path) -> None:
    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    connection = Connection("dev-playback")
    register_speaker(app, connection)
    active = OutputItem(user_id="user-001", session_id="sess-active", priority="normal")
    queued = OutputItem(
        user_id="user-001",
        session_id="sess-queued",
        priority="normal",
        on_blocked="queue",
        ttl_seconds=-1,
    )

    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-active", text="active", intent=active)
    )
    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-queued", text="queued", intent=queued)
    )
    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-active", text="", final=True, intent=active)
    )
    active_stream_id = app.output_service.active_output_stream_id("user-001", "sess-active")
    assert active_stream_id is not None
    close_output_stream(app, session_id="sess-active", stream_id=active_stream_id)

    queued_decisions = session_text(app, "sess-queued", "playback-decisions.jsonl")
    assert "queue" in queued_decisions
    assert "ttl_expired" in queued_decisions


def test_queued_vision_delta_keeps_accumulating_until_playback_turn(tmp_path) -> None:
    """测试目标：验证排队输出保存完整 OutputSource，不丢后续 text delta。

    测试方法：先启动 active 输出，再提交同优先级 queue 输出，并在排队期间继续追加文本；
    active final 后，queued 输出应播放完整文本产生的音频。
    预期结果：queued session 有 `queued_playback_ready` 决策，输出音频大小覆盖两段文本。
    """
    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    connection = Connection("dev-playback")
    register_speaker(app, connection)
    active = OutputItem(user_id="user-001", session_id="sess-active", priority="normal")
    queued = OutputItem(user_id="user-001", session_id="sess-queued", priority="normal", on_blocked="queue")

    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-active", text="active", intent=active)
    )
    before = len(connection.chunks)
    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-queued", text="hello ", intent=queued)
    )
    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-queued", text="world", intent=queued)
    )
    assert len(connection.chunks) == before
    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-queued", text="", final=True, intent=queued)
    )
    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-active", text="", final=True, intent=active)
    )
    active_stream_id = app.output_service.active_output_stream_id("user-001", "sess-active")
    assert active_stream_id is not None
    close_output_stream(app, session_id="sess-active", stream_id=active_stream_id)

    queued_chunks = [chunk for chunk in connection.chunks if chunk.session_id == "sess-queued"]
    queued_decisions = session_text(app, "sess-queued", "playback-decisions.jsonl")
    assert "queued_playback_ready" in queued_decisions
    assert sum(len(chunk.payload) for chunk in queued_chunks) >= 880


def test_endpoint_output_closed_releases_active_and_replays_queued_output(tmp_path) -> None:
    """测试目标：验证端侧播放完成事件会释放播放仲裁 active 状态。

    测试方法：先让当前设备的一条输出保持 active，再提交同用户新会话输出进入队列，
    然后模拟当前设备上报 `stream.output.finished`。
    预期结果：当前 active 被释放且服务端 output stream 被关闭，新会话排队输出恢复播放。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    old_connection = Connection("dev-old")
    new_connection = Connection("dev-new")
    register_speaker(app, old_connection)
    register_speaker(app, new_connection)
    active = OutputItem(user_id="user-001", session_id="sess-old", priority="high")
    queued = OutputItem(user_id="user-001", session_id="sess-new", priority="normal", on_blocked="queue")

    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-old", text="old output", intent=active)
    )
    active_stream_id = app.output_service.active_output_stream_id("user-001", "sess-old")
    assert active_stream_id is not None
    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-new", text="new output", intent=queued)
    )
    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-new", text="", final=True, intent=queued)
    )

    app.publish_control_event(
        Event(
            event_name="stream.output.finished",
            user_id="user-001",
            producer_id="dev-old",
            session_id="sess-old",
            stream_id=active_stream_id,
            stream_type="actuator.speaker",
            payload={"stream_type": "actuator.speaker", "reason": "playback_drained"},
        )
    )

    assert app.stream_service.registry.get(active_stream_id).state == "closed"
    assert app.output_service.active_output_stream_id("user-001", "sess-new") is not None
    queued_decisions = (tmp_path / "runs" / "user-001" / "sess-new" / "playback-decisions.jsonl").read_text()
    assert "queued_playback_ready" in queued_decisions


def test_user_interrupt_discards_queued_native_audio_and_allows_next_turn(tmp_path) -> None:
    """测试目标：验证用户打断会清理尚未播放的排队原生音频。

    测试方法：先让同一实时会话输出一段原生音频并进入等待端侧 finished 的状态，
    再提交第二段原生音频使其进入 queue；随后模拟 provider speech_started 打断，
    最后提交第三段原生音频。
    预期结果：第二段排队音频被 drop，第三段可以重新打开 speaker stream 播放，
    不会因为 `_queued_sessions` 残留而只记录文字/音频 transcript。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    connection = Connection("dev-playback")
    register_speaker(app, connection)
    session_id = "sess-realtime"
    audio_format = StreamFormat(codec="pcm16le", sample_rate=24000, channels=1, chunk_ms=20)

    app.output_service.on_assistant_audio_delta(
        user_id="user-001",
        session_id=session_id,
        audio=b"\x01\x00" * 480,
        format=audio_format,
        final=False,
    )
    app.output_service.on_assistant_audio_delta(
        user_id="user-001",
        session_id=session_id,
        audio=b"",
        format=audio_format,
        final=True,
    )
    first_stream_id = app.output_service.active_output_stream_id("user-001", session_id)
    assert first_stream_id is not None

    app.output_service.on_assistant_audio_delta(
        user_id="user-001",
        session_id=session_id,
        audio=b"\x02\x00" * 480,
        format=audio_format,
        final=False,
    )
    queued_decisions = session_text(app, session_id, "playback-decisions.jsonl")
    assert "active_playback_not_preempted" in queued_decisions

    app.output_service.interrupt_user("user-001", session_id=session_id, reason="provider_speech_started")
    after_interrupt_decisions = session_text(app, session_id, "playback-decisions.jsonl")
    assert "provider_speech_started:discard_queued" in after_interrupt_decisions

    before = len(connection.chunks)
    app.output_service.on_assistant_audio_delta(
        user_id="user-001",
        session_id=session_id,
        audio=b"\x03\x00" * 480,
        format=audio_format,
        final=False,
    )
    assert len(connection.chunks) > before
    latest_decisions = session_text(app, session_id, "playback-decisions.jsonl")
    assert "no_active_playback" in latest_decisions


def test_audio_session_idle_waits_for_endpoint_playback_finished(tmp_path) -> None:
    """测试目标：验证音频会话空闲关闭不会抢在端侧播放完成前发生。

    测试方法：打开连续对话会话，提交一段 TTS 输出并让服务端发出
    `stream.output.finish.requested`，但暂不模拟端侧 `stream.output.finished`。
    预期结果：维护任务在端侧仍有待播放 stream 时不关闭会话；端侧回报播放完成后，
    空闲计时从播放完成点重新开始。
    """

    app = RealtimeAgentApp(
        RealtimeAgentConfig(
            runs_root=str(tmp_path / "runs"),
            audio_session_idle_timeout_seconds=1,
            agent_mode="vision",
        )
    )
    connection = Connection("dev-playback")
    register_speaker(app, connection)
    app.publish_control_event(
        Event(
            event_name="control.audio_session.opened",
            user_id="user-001",
            producer_id="dev-playback",
            session_id="dev-playback",
            payload={"reason": "browser_device_opened"},
        )
    )

    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="dev-playback", text="长回复")
    )
    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="dev-playback", text="", final=True)
    )
    stream_id = next(event.stream_id for event in connection.events if event.event_name == "stream.output.finish.requested")
    app.publish_control_event(
        Event(
            event_name="stream.output.started",
            user_id="user-001",
            producer_id="dev-playback",
            session_id="dev-playback",
            stream_id=stream_id,
            stream_type="actuator.speaker",
            payload={"stream_type": "actuator.speaker"},
        )
    )
    state = app._device_dialogs_by_user["user-001"]
    assert stream_id in state.endpoint_playback_stream_ids

    result = app.run_maintenance_once(now=state.last_activity_at + 2)
    assert result["closed_audio_sessions"] == []
    assert not any(event.event_name == "control.audio_session.close.requested" for event in connection.events)

    app.publish_control_event(
        Event(
            event_name="stream.output.finished",
            user_id="user-001",
            producer_id="dev-playback",
            session_id="dev-playback",
            stream_id=stream_id,
            stream_type="actuator.speaker",
            payload={"stream_type": "actuator.speaker"},
        )
    )
    state = app._device_dialogs_by_user["user-001"]
    assert stream_id not in state.endpoint_playback_stream_ids
    assert app.run_maintenance_once(now=state.last_activity_at + 2)["closed_audio_sessions"] == ["dev-playback"]


def test_explicit_on_blocked_drop_is_not_overridden_by_global_queue_default(tmp_path) -> None:
    """测试目标：验证显式 `on_blocked="drop"` 不会被全局默认 queue 覆盖。

    测试方法：先启动 active 输出，再提交同优先级且显式 drop 的 blocked 输出。
    预期结果：blocked 输出被 drop，不进入队列，也不会产生第二条 speaker stream。
    """
    app = RealtimeAgentApp(
        RealtimeAgentConfig(
            runs_root=str(tmp_path / "runs"),
            output_default_on_blocked="queue",
        )
    )
    connection = Connection("dev-playback")
    register_speaker(app, connection)
    active = OutputItem(user_id="user-001", session_id="sess-active", priority="normal")
    blocked = OutputItem(user_id="user-001", session_id="sess-blocked", priority="normal", on_blocked="drop")

    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-active", text="active", intent=active)
    )
    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-blocked", text="blocked", intent=blocked)
    )

    blocked_decisions = [
        json.loads(line)
        for line in session_text(app, "sess-blocked", "playback-decisions.jsonl").splitlines()
        if line.strip()
    ]
    assert blocked_decisions[-1]["action"] == "drop"
    assert blocked_decisions[-1]["reason"] == "active_playback_not_preempted"
    assert {chunk.session_id for chunk in connection.chunks} == {"sess-active"}


def test_native_audio_empty_done_does_not_open_output_stream(tmp_path) -> None:
    """测试目标：验证原生音频只有 done、没有 delta 时不会打开空播放流。

    测试方法：直接调用 Output Service 的 native audio 入口，传入 `final=True` 和空
    audio payload。
    预期结果：端侧不会收到 `stream.output.start.requested`，runs 中记录 empty_output。
    """
    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    connection = Connection("dev-playback")
    register_speaker(app, connection)

    app.output_service.on_assistant_audio_delta(
        user_id="user-001",
        session_id="sess-native-empty",
        audio=b"",
        format=StreamFormat(codec="pcm16le", sample_rate=24000, channels=1, chunk_ms=20),
        final=True,
        metadata={"provider": "fake"},
    )

    assert not any(event.event_name == "stream.output.start.requested" for event in connection.events)
    model_events = session_text(app, "sess-native-empty", "model-events.jsonl")
    assert "assistant_audio.done" in model_events
    assert '"empty_output": true' in model_events


def test_native_audio_delta_is_split_by_stream_format_chunk_size(tmp_path) -> None:
    """测试目标：验证 Omni 原生大音频包会按 StreamFormat 拆成多片下发。

    测试方法：提交 15360 bytes 的 24k PCM 原生音频，等价于 provider 一次返回约
    320ms 音频。
    预期结果：Output Service 按 20ms/960 bytes 拆分，避免超过 stream.max_chunk_bytes，
    并且每片都声明 24k/20ms。
    """
    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), stream_max_chunk_bytes=8192))
    connection = Connection("dev-playback")
    register_speaker(app, connection)

    app.output_service.on_assistant_audio_delta(
        user_id="user-001",
        session_id="sess-native-large",
        audio=b"\x01\x00" * 7680,
        format=StreamFormat(codec="pcm16le", sample_rate=24000, channels=1, chunk_ms=20),
        final=False,
        metadata={"provider": "fake"},
    )

    assert len(connection.chunks) == 16
    assert {len(chunk.payload) for chunk in connection.chunks} == {960}
    assert {chunk.sample_rate for chunk in connection.chunks} == {24000}
    assert {chunk.duration_ms for chunk in connection.chunks} == {20}
    model_events = session_text(app, "sess-native-large", "model-events.jsonl")
    assert '"chunk_count": 16' in model_events


def test_tts_metrics_stream_format_and_chunk_format_match(tmp_path) -> None:
    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), tts_provider="mock"))
    app.output_service = OutputService(
        stream_service=app.stream_service,
        recorder=app.recorder,
        tts_config=TtsProviderConfig(provider="mock", sample_rate_hz=22050),
    )
    connection = Connection("dev-playback")
    register_speaker(app, connection)

    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-format", text="format check")
    )

    chunk = connection.chunks[0]
    stream_id = chunk.stream_id
    handle = app.stream_service.registry.get(stream_id)
    model_events = session_text(app, "sess-format", "model-events.jsonl")
    assert handle.format.sample_rate == 22050
    assert chunk.sample_rate == 22050
    assert '"sample_rate_hz": 22050' in model_events


def test_each_output_stream_gets_independent_tts_session(tmp_path) -> None:
    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    connection = Connection("dev-playback")
    register_speaker(app, connection)

    app.output_service.submit_output(OutputItem(user_id="user-001", session_id="sess-one"), "one")
    first_stream_id = app.output_service.active_output_stream_id("user-001", "sess-one")
    assert first_stream_id is not None
    close_output_stream(app, session_id="sess-one", stream_id=first_stream_id)
    app.output_service.submit_output(OutputItem(user_id="user-001", session_id="sess-two"), "two")

    stream_ids = {chunk.stream_id for chunk in connection.chunks}
    assert len(stream_ids) == 2


def test_cached_prompt_audio_reuses_audio_and_records_wav(tmp_path) -> None:
    """测试目标：验证缓存提示音复用音频，并沉淀为可回放 wav。

    测试方法：两次使用同一个 cache_key 提交提示音，读取端侧 chunk、agent 事件和 wav 文件。
    预期结果：两次输出字节一致，第二次命中缓存，两个会话都生成 wav 回放产物。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    connection = Connection("dev-playback")
    register_speaker(app, connection)
    fmt = StreamFormat(codec="pcm16le", sample_rate=16000, channels=1, chunk_ms=40)

    first = app.output_service.submit_cached_prompt_audio(
        user_id="user-001",
        session_id="sess-cache-one",
        cache_key="timer-done",
        text="timer done",
        format=fmt,
    )
    first_stream_id = app.output_service.active_output_stream_id("user-001", "sess-cache-one")
    assert first_stream_id is not None
    close_output_stream(app, session_id="sess-cache-one", stream_id=first_stream_id)
    second = app.output_service.submit_cached_prompt_audio(
        user_id="user-001",
        session_id="sess-cache-two",
        cache_key="timer-done",
        text="changed text should not regenerate",
        format=fmt,
    )

    assert first.action == "play_now"
    assert second.action == "play_now"
    assert len(connection.chunks) == 2
    assert connection.chunks[0].payload == connection.chunks[1].payload
    first_events = session_text(app, "sess-cache-one", "model-events.jsonl")
    second_events = session_text(app, "sess-cache-two", "model-events.jsonl")
    assert '"cached": false' in first_events
    assert '"cached": true' in second_events
    wavs = list(app.recorder.media_dir("sess-cache-two", "actuator.speaker").glob("output-*.wav"))
    assert len(wavs) == 1
    with wave.open(str(wavs[0]), "rb") as handle:
        assert handle.getframerate() == 16000
        assert handle.getnchannels() == 1
        assert handle.getnframes() > 0


def test_notification_coordinator_respects_task_signal_notify_and_agent_sync(tmp_path) -> None:
    """测试目标：验证 TaskSignal 能区分直接通知和 Agent 上下文同步。

    测试方法：构造一个禁止直接播报、但要求 Agent 决策的任务信号，经 TaskSignalBridge 处理。
    预期结果：不产生端侧音频，task signal 和 agent sync 事件都会写入 runs。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    connection = Connection("dev-playback")
    register_speaker(app, connection)
    bridge = TaskSignalBridge(recorder=app.recorder, output_service=app.output_service)
    signal = TaskSignal(
        task_id="task-nav-001",
        task_type="navigation",
        signal_name="reroute_required",
        user_id="user-001",
        session_id="sess-task",
        payload={"text": "需要重新规划路线"},
        requires_agent_decision=True,
        allow_direct_notify=False,
    )

    bridge.handle_signal(signal)

    assert connection.chunks == []
    task_signals = session_text(app, "sess-task", "task-signals.jsonl")
    agent_events = session_text(app, "sess-task", "agent-events.jsonl")
    assert "reroute_required" in task_signals
    assert "task.requires_agent_context_sync" in agent_events


def test_notification_dedupe_and_merge_decisions_are_observable(tmp_path) -> None:
    """测试目标：验证通知协调层的去重、合并和可观测决策。

    测试方法：提交重复 dedupe_key 通知，再提交两个 merge_key 相同的通知并 flush。
    预期结果：重复通知被丢弃，合并通知只播报一次，debug snapshot 暴露通知决策。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    connection = Connection("dev-playback")
    register_speaker(app, connection)
    coordinator = app.output_service.notification_coordinator

    first = coordinator.submit(
        NotificationRequest(
            user_id="user-001",
            session_id="sess-notify-one",
            text="first",
            dedupe_key="notify-1",
        )
    )
    first_stream_id = app.output_service.active_output_stream_id("user-001", "sess-notify-one")
    assert first_stream_id is not None
    close_output_stream(app, session_id="sess-notify-one", stream_id=first_stream_id)
    duplicate = coordinator.submit(
        NotificationRequest(
            user_id="user-001",
            session_id="sess-notify-one",
            text="duplicate",
            dedupe_key="notify-1",
        )
    )
    coordinator.submit(
        NotificationRequest(
            user_id="user-001",
            session_id="sess-notify-merge",
            text="left",
            merge_key="traffic",
            merge_window_seconds=5,
        )
    )
    merged = coordinator.submit(
        NotificationRequest(
            user_id="user-001",
            session_id="sess-notify-merge",
            text="right",
            merge_key="traffic",
            merge_window_seconds=5,
        )
    )
    flushed = coordinator.flush_merge("traffic")

    assert first.action == "route"
    assert duplicate.action == "drop"
    assert merged.action == "merge"
    assert flushed is not None and flushed.action == "route"
    assert any(chunk.session_id == "sess-notify-merge" for chunk in connection.chunks)
    snapshot = app.output_service.debug_snapshot()
    actions = [decision["action"] for decision in snapshot["notifications"]["recent_decisions"]]
    assert {"route", "drop", "merge"}.issubset(actions)


def test_playback_debug_snapshot_records_active_queue_and_decisions(tmp_path) -> None:
    """测试目标：验证播放仲裁调试快照包含 active、queue 和最近决策。

    测试方法：让一个 normal 输出占用播放，再提交同优先级 queue 输出后读取 debug snapshot。
    预期结果：快照和磁盘文件都包含当前 active 与排队输出。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    connection = Connection("dev-playback")
    register_speaker(app, connection)
    active = OutputItem(user_id="user-001", session_id="sess-active-debug", priority="normal")
    queued = OutputItem(user_id="user-001", session_id="sess-queued-debug", priority="normal", on_blocked="queue")

    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-active-debug", text="active", intent=active)
    )
    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-queued-debug", text="queued", intent=queued)
    )

    snapshot = app.output_service.debug_snapshot()
    assert snapshot["active"]["user-001"]["session_id"] == "sess-active-debug"
    assert snapshot["queued"]["user-001"][0]["session_id"] == "sess-queued-debug"
    assert any(decision["action"] == "queue" for decision in snapshot["recent_decisions"])
    saved = json.loads((tmp_path / "runs" / "debug" / "playback.json").read_text())
    assert saved["active"]["user-001"]["session_id"] == "sess-active-debug"


def test_native_output_finish_does_not_clear_active_vision_tts_stream(tmp_path) -> None:
    """测试目标：验证直接通知音频结束不会清掉同会话正在播放的 Text TTS stream。

    测试方法：先启动一条 Text TTS 输出流，再模拟 Task 直接通知通过 native audio
    入口写入并结束，随后继续提交 Text delta 和 final。
    预期结果：native source mismatch 只记录忽略，不会删除 Text TTS 的 active source；
    Text final 仍能 flush 尾音并正常写出 stream.output.summary。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), tts_provider="mock"))
    app.output_service.router._injected_tts = FinishPayloadTTS()
    connection = Connection("dev-playback")
    register_speaker(app, connection)
    session_id = "sess-mixed-output"

    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id=session_id, text="助手长回复开头")
    )
    active_stream_id = connection.chunks[-1].stream_id

    app.output_service.submit_output(
        OutputItem(user_id="user-001", session_id=session_id, priority="normal"),
        "任务失败通知",
    )
    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id=session_id, text="助手长回复结尾")
    )
    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id=session_id, text="", final=True)
    )

    model_events = session_text(app, session_id, "model-events.jsonl")
    stream_events = session_text(app, session_id, "stream-events.jsonl")
    assert "source_no_longer_active" in model_events
    assert "stream_no_longer_active" not in model_events
    assert "stream.output.summary" in stream_events
    assert f'"stream_id": "{active_stream_id}"' in stream_events
    assert any(event.event_name == "stream.output.finish.requested" for event in connection.events)
