import json
import wave

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.output import AssistantTextDelta
from audio_chat.output.service import NotificationRequest, OutputItem, MockStreamingTTS, OutputService, TtsProviderConfig
from audio_chat.protocol import Event, StreamChunk, StreamFormat
from audio_chat.tasks import TaskEvent, TaskEventBridge


class Connection:
    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.events = []
        self.chunks = []

    def push_event(self, event: Event) -> None:
        self.events.append(event)

    def push_stream_chunk(self, chunk: StreamChunk) -> None:
        self.chunks.append(chunk)


def register_speaker(app: AudioChatApp, connection: Connection, user_id: str = "user-001") -> None:
    app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id=user_id,
            producer_id=connection.device_id,
            payload={
                "device_id": connection.device_id,
                "auth": {"mode": "disabled"},
                "subscriptions": [
                    {"event": "control.audio_session.*"},
                    {"event": "stream.output.*", "filter": {"stream_type": "actuator.speaker"}},
                ],
            },
        ),
        connection,
    )


def test_text_agent_streams_text_and_tts_audio_before_final_done(tmp_path) -> None:
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
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

    model_events = (tmp_path / "runs" / "sessions" / handle.session_id / "model-events.jsonl").read_text()
    assert model_events.index("assistant_audio.delta") < model_events.index('"final": true')
    assert model_events.count("assistant_text.delta") >= 2
    assert len(connection.chunks) >= 2


def test_playback_arbiter_interrupts_lower_priority_stream(tmp_path) -> None:
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
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

    app.output_service.on_assistant_text_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-output", text="low priority", intent=low)
    )
    app.output_service.on_assistant_text_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-output-2", text="critical priority", intent=high)
    )

    decisions = (tmp_path / "runs" / "sessions" / "sess-output" / "playback-decisions.jsonl").read_text()
    assert "interrupt" in decisions
    assert any(event.event_name == "stream.output.cancelled" for event in connection.events)


def test_user_interrupt_cancels_current_output_stream(tmp_path) -> None:
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    connection = Connection("dev-playback")
    register_speaker(app, connection)
    intent = OutputItem(user_id="user-001", session_id="sess-interrupt", priority="normal")

    app.output_service.on_assistant_text_delta(
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
    assert "stream.output.cancelled" in event_names
    decisions = (tmp_path / "runs" / "sessions" / "sess-interrupt" / "playback-decisions.jsonl").read_text()
    assert "cancel_current" in decisions


def test_requeue_plays_after_interrupting_stream_finishes(tmp_path) -> None:
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
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

    app.output_service.on_assistant_text_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-low", text="low", intent=low)
    )
    app.output_service.on_assistant_text_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-high", text="high", intent=high)
    )
    app.output_service.on_assistant_text_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-high", text="", final=True, intent=high)
    )

    low_decisions = (tmp_path / "runs" / "sessions" / "sess-low" / "playback-decisions.jsonl").read_text()
    high_decisions = (tmp_path / "runs" / "sessions" / "sess-high" / "playback-decisions.jsonl").read_text()
    assert "play_now" in low_decisions
    assert "interrupt" in high_decisions
    assert len([event for event in connection.events if event.event_name == "stream.output.open.requested"]) >= 3


def test_queue_ttl_expiry_and_same_priority_no_interrupt(tmp_path) -> None:
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
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

    app.output_service.on_assistant_text_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-active", text="active", intent=active)
    )
    app.output_service.on_assistant_text_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-queued", text="queued", intent=queued)
    )
    app.output_service.on_assistant_text_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-active", text="", final=True, intent=active)
    )

    queued_decisions = (tmp_path / "runs" / "sessions" / "sess-queued" / "playback-decisions.jsonl").read_text()
    assert "queue" in queued_decisions
    assert "ttl_expired" in queued_decisions


def test_queued_text_delta_keeps_accumulating_until_playback_turn(tmp_path) -> None:
    """测试目标：验证排队输出保存完整 OutputSource，不丢后续 text delta。

    测试方法：先启动 active 输出，再提交同优先级 queue 输出，并在排队期间继续追加文本；
    active final 后，queued 输出应播放完整文本产生的音频。
    预期结果：queued session 有 `queued_playback_ready` 决策，输出音频大小覆盖两段文本。
    """
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    connection = Connection("dev-playback")
    register_speaker(app, connection)
    active = OutputItem(user_id="user-001", session_id="sess-active", priority="normal")
    queued = OutputItem(user_id="user-001", session_id="sess-queued", priority="normal", on_blocked="queue")

    app.output_service.on_assistant_text_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-active", text="active", intent=active)
    )
    before = len(connection.chunks)
    app.output_service.on_assistant_text_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-queued", text="hello ", intent=queued)
    )
    app.output_service.on_assistant_text_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-queued", text="world", intent=queued)
    )
    assert len(connection.chunks) == before
    app.output_service.on_assistant_text_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-queued", text="", final=True, intent=queued)
    )
    app.output_service.on_assistant_text_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-active", text="", final=True, intent=active)
    )

    queued_chunks = [chunk for chunk in connection.chunks if chunk.session_id == "sess-queued"]
    queued_decisions = (tmp_path / "runs" / "sessions" / "sess-queued" / "playback-decisions.jsonl").read_text()
    assert "queued_playback_ready" in queued_decisions
    assert sum(len(chunk.payload) for chunk in queued_chunks) >= 880


def test_explicit_on_blocked_drop_is_not_overridden_by_global_queue_default(tmp_path) -> None:
    """测试目标：验证显式 `on_blocked="drop"` 不会被全局默认 queue 覆盖。

    测试方法：先启动 active 输出，再提交同优先级且显式 drop 的 blocked 输出。
    预期结果：blocked 输出被 drop，不进入队列，也不会产生第二条 speaker stream。
    """
    app = AudioChatApp(
        AudioChatConfig(
            runs_root=str(tmp_path / "runs"),
            output_default_on_blocked="queue",
        )
    )
    connection = Connection("dev-playback")
    register_speaker(app, connection)
    active = OutputItem(user_id="user-001", session_id="sess-active", priority="normal")
    blocked = OutputItem(user_id="user-001", session_id="sess-blocked", priority="normal", on_blocked="drop")

    app.output_service.on_assistant_text_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-active", text="active", intent=active)
    )
    app.output_service.on_assistant_text_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-blocked", text="blocked", intent=blocked)
    )

    blocked_decisions = [
        json.loads(line)
        for line in (tmp_path / "runs" / "sessions" / "sess-blocked" / "playback-decisions.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert blocked_decisions[-1]["action"] == "drop"
    assert blocked_decisions[-1]["reason"] == "active_playback_not_preempted"
    assert {chunk.session_id for chunk in connection.chunks} == {"sess-active"}


def test_native_audio_empty_done_does_not_open_output_stream(tmp_path) -> None:
    """测试目标：验证原生音频只有 done、没有 delta 时不会打开空播放流。

    测试方法：直接调用 Output Service 的 native audio 入口，传入 `final=True` 和空
    audio payload。
    预期结果：端侧不会收到 `stream.output.open.requested`，runs 中记录 empty_output。
    """
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
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

    assert not any(event.event_name == "stream.output.open.requested" for event in connection.events)
    model_events = (tmp_path / "runs" / "sessions" / "sess-native-empty" / "model-events.jsonl").read_text()
    assert "assistant_audio.done" in model_events
    assert '"empty_output": true' in model_events


def test_native_audio_delta_is_split_by_stream_format_chunk_size(tmp_path) -> None:
    """测试目标：验证 Omni 原生大音频包会按 StreamFormat 拆成多片下发。

    测试方法：提交 15360 bytes 的 24k PCM 原生音频，等价于 provider 一次返回约
    320ms 音频。
    预期结果：Output Service 按 20ms/960 bytes 拆分，避免超过 stream.max_chunk_bytes，
    并且每片都声明 24k/20ms。
    """
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), stream_max_chunk_bytes=8192))
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
    model_events = (tmp_path / "runs" / "sessions" / "sess-native-large" / "model-events.jsonl").read_text()
    assert '"chunk_count": 16' in model_events


def test_tts_metrics_stream_format_and_chunk_format_match(tmp_path) -> None:
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), tts_provider="mock"))
    app.output_service = OutputService(
        stream_service=app.stream_service,
        recorder=app.recorder,
        tts_config=TtsProviderConfig(provider="mock", sample_rate_hz=22050),
    )
    connection = Connection("dev-playback")
    register_speaker(app, connection)

    app.output_service.on_assistant_text_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-format", text="format check")
    )

    chunk = connection.chunks[0]
    stream_id = chunk.stream_id
    handle = app.stream_service.registry.get(stream_id)
    model_events = (tmp_path / "runs" / "sessions" / "sess-format" / "model-events.jsonl").read_text()
    assert handle.format.sample_rate == 22050
    assert chunk.sample_rate == 22050
    assert '"sample_rate_hz": 22050' in model_events


def test_each_output_stream_gets_independent_tts_session(tmp_path) -> None:
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    connection = Connection("dev-playback")
    register_speaker(app, connection)

    app.output_service.submit_output(OutputItem(user_id="user-001", session_id="sess-one"), "one")
    app.output_service.submit_output(OutputItem(user_id="user-001", session_id="sess-two"), "two")

    stream_ids = {chunk.stream_id for chunk in connection.chunks}
    assert len(stream_ids) == 2


def test_cached_prompt_audio_reuses_audio_and_records_wav(tmp_path) -> None:
    """测试目标：验证缓存提示音复用音频，并沉淀为可回放 wav。

    测试方法：两次使用同一个 cache_key 提交提示音，读取端侧 chunk、agent 事件和 wav 文件。
    预期结果：两次输出字节一致，第二次命中缓存，两个会话都生成 wav 回放产物。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
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
    first_events = (tmp_path / "runs" / "sessions" / "sess-cache-one" / "model-events.jsonl").read_text()
    second_events = (tmp_path / "runs" / "sessions" / "sess-cache-two" / "model-events.jsonl").read_text()
    assert '"cached": false' in first_events
    assert '"cached": true' in second_events
    wavs = list((tmp_path / "runs" / "sessions" / "sess-cache-two").glob("output-*.wav"))
    assert len(wavs) == 1
    with wave.open(str(wavs[0]), "rb") as handle:
        assert handle.getframerate() == 16000
        assert handle.getnchannels() == 1
        assert handle.getnframes() > 0


def test_notification_coordinator_respects_task_event_notify_and_agent_sync(tmp_path) -> None:
    """测试目标：验证 TaskEvent 能区分直接通知和 Agent 上下文同步。

    测试方法：构造一个禁止直接播报、但要求 Agent 决策的任务事件，经 TaskEventBridge 处理。
    预期结果：不产生端侧音频，task event 和 agent sync 事件都会写入 runs。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    connection = Connection("dev-playback")
    register_speaker(app, connection)
    bridge = TaskEventBridge(recorder=app.recorder, output_service=app.output_service)
    event = TaskEvent(
        task_id="task-nav-001",
        task_type="navigation",
        event_name="reroute_required",
        user_id="user-001",
        session_id="sess-task",
        payload={"text": "需要重新规划路线"},
        requires_agent_decision=True,
        allow_direct_notify=False,
    )

    bridge.handle_event(event)

    assert connection.chunks == []
    task_events = (tmp_path / "runs" / "sessions" / "sess-task" / "task-events.jsonl").read_text()
    agent_events = (tmp_path / "runs" / "sessions" / "sess-task" / "agent-events.jsonl").read_text()
    assert "reroute_required" in task_events
    assert "task.requires_agent_context_sync" in agent_events


def test_notification_dedupe_and_merge_decisions_are_observable(tmp_path) -> None:
    """测试目标：验证通知协调层的去重、合并和可观测决策。

    测试方法：提交重复 dedupe_key 通知，再提交两个 merge_key 相同的通知并 flush。
    预期结果：重复通知被丢弃，合并通知只播报一次，debug snapshot 暴露通知决策。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
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

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    connection = Connection("dev-playback")
    register_speaker(app, connection)
    active = OutputItem(user_id="user-001", session_id="sess-active-debug", priority="normal")
    queued = OutputItem(user_id="user-001", session_id="sess-queued-debug", priority="normal", on_blocked="queue")

    app.output_service.on_assistant_text_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-active-debug", text="active", intent=active)
    )
    app.output_service.on_assistant_text_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-queued-debug", text="queued", intent=queued)
    )

    snapshot = app.output_service.debug_snapshot()
    assert snapshot["active"]["user-001"]["session_id"] == "sess-active-debug"
    assert snapshot["queued"]["user-001"][0]["session_id"] == "sess-queued-debug"
    assert any(decision["action"] == "queue" for decision in snapshot["recent_decisions"])
    saved = json.loads((tmp_path / "runs" / "debug" / "playback.json").read_text())
    assert saved["active"]["user-001"]["session_id"] == "sess-active-debug"
