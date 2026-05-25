from __future__ import annotations

import audioop
import json
import time

from realtime_agent.app import RealtimeAgentApp, RealtimeAgentConfig
from realtime_agent.output import AssistantTextDelta
from realtime_agent.output.service import MockStreamingTTS, TtsProviderConfig, build_tts_provider
from realtime_agent.protocol import Event, StreamChunk, StreamFormat


class Connection:
    """测试用端侧连接。"""

    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.events = []
        self.chunks = []

    def push_event(self, event: Event) -> None:
        """记录控制事件。"""

        self.events.append(event)

    def push_stream_chunk(self, chunk: StreamChunk) -> None:
        """记录输出音频。"""

        self.chunks.append(chunk)


def register_speaker(app: RealtimeAgentApp, connection: Connection, user_id: str = "user-001") -> None:
    """注册一个可消费 speaker stream 的测试设备。"""

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


def test_mock_streaming_tts_records_first_chunk_latency_metrics() -> None:
    """测试目标：验证 Streaming TTS 暴露首文本和首音频延迟指标。

    测试方法：直接使用 mock TTS 合成一段文本并读取 metrics。
    预期结果：`first_text_at`、`first_audio_at`、`first_chunk_latency_ms` 都有值。
    """

    tts = MockStreamingTTS(sample_rate_hz=16000)
    audio = tts.synthesize_delta("hello")
    metrics = tts.metrics()

    assert audio
    assert audioop.rms(audio, 2) > 1000
    assert metrics["first_text_at"] is not None
    assert metrics["first_audio_at"] is not None
    assert metrics["first_chunk_latency_ms"] is not None
    assert metrics["tts_first_audio_latency_ms"] == metrics["first_chunk_latency_ms"]


def test_output_service_persists_tts_latency_metrics_in_audio_delta_event(tmp_path) -> None:
    """测试目标：验证文本 delta 持续进入 TTS 后，runs 中能看到首包延迟指标。

    测试方法：注册 speaker 端侧，提交一段 assistant text delta。
    预期结果：端侧收到音频，`model-events.jsonl` 中记录 `first_chunk_latency_ms`。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), tts_provider="mock"))
    connection = Connection("dev-speaker")
    register_speaker(app, connection)

    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-tts", text="hello")
    )

    assert connection.chunks
    events = [
        json.loads(line)
        for line in (tmp_path / "runs" / "user-001" / "sess-tts" / "model-events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    audio_event = next(item for item in events if item.get("event") == "assistant_audio.delta")
    assert audio_event["tts"]["first_chunk_latency_ms"] is not None


def test_output_service_flushes_streaming_tts_audio_on_final(tmp_path) -> None:
    """测试目标：验证流式 TTS 在 final 后产生的剩余音频不会丢失。

    测试方法：注入一个普通 text delta 只记录文本、不立即返回音频的 TTS；
    当 assistant final 到达时，TTS 的 `finish()` 返回完整 PCM。
    预期结果：OutputService 在关闭 stream 前把 finish 返回的音频写给端侧。
    """

    class FinishFlushTTS:
        """测试用 TTS，模拟真实 provider 在 streaming_complete 后返回尾部音频。"""

        provider_name = "finish-flush"
        model = "finish-flush-model"
        streaming = True

        def __init__(self) -> None:
            self.texts: list[str] = []
            self.finished = False

        def synthesize_delta(self, text: str) -> bytes:
            """记录增量文本，但不立即返回音频。"""

            if text:
                self.texts.append(text)
            return b""

        def drain_audio(self, wait_seconds: float = 0.0) -> bytes:
            """普通 delta 阶段没有可用音频。"""

            return b""

        def finish(self) -> bytes:
            """在 final 阶段完成当前 TTS streaming task 并返回 PCM。"""

            self.finished = True
            return b"\x01\x00" * 960

        def metrics(self) -> dict:
            """返回固定音频格式和调试信息。"""

            return {"provider": self.provider_name, "model": self.model, "sample_rate_hz": 24000}

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), tts_provider="mock"))
    connection = Connection("dev-speaker")
    register_speaker(app, connection)
    fake_tts = FinishFlushTTS()
    app.output_service.router._injected_tts = fake_tts

    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-flush", text="第一句")
    )
    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-flush", text="", final=True)
    )

    assert fake_tts.texts == ["第一句"]
    assert fake_tts.finished
    assert sum(len(chunk.payload) for chunk in connection.chunks) == 1920
    assert any(event.event_name == "stream.output.finish.requested" for event in connection.events)


def test_output_service_completes_tts_task_on_each_answer_final(tmp_path) -> None:
    """测试目标：验证每轮回答 final 都会完成当前 TTS task。

    测试方法：同一个 session 连续提交两轮文本输出，分别记录两轮 TTS task。
    预期结果：每轮 final 都调用 provider complete，并在下一轮创建新的 TTS task。
    """

    class CountingTTS:
        """测试用 TTS：记录每个回答 task 的文本和 complete 次数。"""

        provider_name = "counting"
        model = "counting-model"
        streaming = True

        def __init__(self) -> None:
            self.texts: list[str] = []
            self.finished = False

        def synthesize_delta(self, text: str) -> bytes:
            """记录本 task 收到的文本并同步返回音频。"""

            self.texts.append(text)
            return b"\x03\x00" * 240

        def drain_audio(self, wait_seconds: float = 0.0) -> bytes:
            """本测试不依赖后台音频。"""

            return b""

        def finish(self) -> bytes:
            """标记当前 task 已完成。"""

            self.finished = True
            return b""

        def metrics(self) -> dict:
            """返回固定音频格式。"""

            return {"provider": self.provider_name, "model": self.model, "sample_rate_hz": 24000}

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), tts_provider="mock"))
    connection = Connection("dev-speaker")
    register_speaker(app, connection)
    router = app.output_service.router
    tasks = [CountingTTS(), CountingTTS()]

    def next_tts() -> CountingTTS:
        """按顺序返回两个独立 task。"""

        return tasks.pop(0)

    router._new_tts = next_tts  # type: ignore[method-assign]

    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-task", text="第一轮")
    )
    first_task = router._source_by_session["sess-task"].tts
    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-task", text="", final=True)
    )

    assert first_task.finished
    assert "sess-task" not in router._source_by_session
    first_finish = next(event for event in connection.events if event.event_name == "stream.output.finish.requested")
    app.publish_control_event(
        Event(
            event_name="stream.output.closed",
            user_id="user-001",
            producer_id="dev-speaker",
            session_id="sess-task",
            stream_id=first_finish.stream_id,
            stream_type="actuator.speaker",
            payload={"reason": "test_endpoint_drain_done"},
        )
    )

    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-task", text="第二轮")
    )
    second_task = router._source_by_session["sess-task"].tts

    assert second_task is not first_task
    assert second_task.texts == ["第二轮"]
    assert connection.chunks


def test_output_endpoint_ack_timeout_releases_active_playback(tmp_path) -> None:
    """测试目标：验证端侧不回 output closed 时 Server 会超时释放播放状态。

    测试方法：提交一轮文本并 final，只让 Server 下发 `stream.output.finish.requested`，
    不模拟端侧 `stream.output.closed`，随后触发维护任务。
    预期结果：stream 被标记为 failed，active playback 被释放，并记录 endpoint ack timeout。
    """

    app = RealtimeAgentApp(
        RealtimeAgentConfig(
            runs_root=str(tmp_path / "runs"),
            tts_provider="mock",
            output_endpoint_ack_timeout_seconds=0.01,
        )
    )
    connection = Connection("dev-speaker")
    register_speaker(app, connection, user_id="user-timeout")

    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-timeout", session_id="sess-timeout", text="需要回执")
    )
    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-timeout", session_id="sess-timeout", text="", final=True)
    )
    finish_event = next(event for event in connection.events if event.event_name == "stream.output.finish.requested")

    result = app.run_maintenance_once(now=time.time() + 1)

    assert finish_event.stream_id in result["output_endpoint_ack_timeouts"]
    assert app.output_service.active_output_stream_id("user-timeout", "sess-timeout") is None
    assert app.stream_service.registry.get(finish_event.stream_id).state == "failed"
    stream_events = (tmp_path / "runs" / "user-timeout" / "sess-timeout" / "stream-events.jsonl").read_text(
        encoding="utf-8"
    )
    assert "stream.output.endpoint_ack.timeout" in stream_events


def test_output_service_background_drains_tts_audio_between_vision_deltas(tmp_path) -> None:
    """测试目标：验证 TTS 回调音频不必等下一次 text delta 或 final 才下发。

    测试方法：注入一个 `synthesize_delta()` 不返回音频、但 `drain_audio()` 返回音频的
    TTS，提交一段非 final 文本后短暂等待后台 pump。
    预期结果：端侧在 final 前收到音频 chunk。
    """

    class BackgroundDrainTTS:
        """测试用 TTS，模拟 provider 通过后台回调产生音频。"""

        provider_name = "background-drain"
        model = "background-drain-model"
        streaming = True

        def __init__(self) -> None:
            self.texts: list[str] = []
            self.drained = False

        def synthesize_delta(self, text: str) -> bytes:
            """只接收文本，不同步返回音频。"""

            if text:
                self.texts.append(text)
            return b""

        def drain_audio(self, wait_seconds: float = 0.0) -> bytes:
            """第一次后台 drain 返回一段 PCM。"""

            if self.texts and not self.drained:
                self.drained = True
                return b"\x02\x00" * 480
            return b""

        def finish(self) -> bytes:
            """本测试不依赖 final flush。"""

            return b""

        def metrics(self) -> dict:
            """返回固定音频格式。"""

            return {"provider": self.provider_name, "model": self.model, "sample_rate_hz": 24000}

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), tts_provider="mock"))
    connection = Connection("dev-speaker")
    register_speaker(app, connection)
    fake_tts = BackgroundDrainTTS()
    app.output_service.router._injected_tts = fake_tts

    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-background", text="后台音频")
    )
    deadline = time.time() + 1.0
    while time.time() < deadline and not connection.chunks:
        time.sleep(0.02)

    assert fake_tts.texts == ["后台音频"]
    assert connection.chunks
    assert sum(len(chunk.payload) for chunk in connection.chunks) == 960


def test_vision_tts_empty_final_does_not_open_output_stream(tmp_path) -> None:
    """测试目标：验证没有任何文本 delta 时，空 final 不会打开扬声器输出流。

    测试方法：直接提交 `text=""、final=True` 的 AssistantTextDelta。
    预期结果：端侧不会收到 `stream.output.open.requested`，runs 记录 empty_output。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), tts_provider="mock"))
    connection = Connection("dev-speaker")
    register_speaker(app, connection)

    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-empty-final", text="", final=True)
    )

    assert not any(event.event_name == "stream.output.open.requested" for event in connection.events)
    model_events = (tmp_path / "runs" / "user-001" / "sess-empty-final" / "model-events.jsonl").read_text(
        encoding="utf-8"
    )
    assert "assistant_audio.done" in model_events
    assert '"empty_output": true' in model_events


def test_vision_tts_failure_releases_active_playback_for_next_turn(tmp_path) -> None:
    """测试目标：验证 TTS 在 output stream 打开后失败时，会释放 active 播放状态。

    测试方法：先注入一个 `synthesize_delta()` 抛异常的 TTS，触发输出失败；再换成
    正常 mock TTS 提交下一轮文本。
    预期结果：第二轮播放决策是 `play_now`，不是 `active_playback_not_preempted` 队列。
    """

    class FailingTTS:
        """测试用 TTS：模拟 provider 抛出 speech synthesizer 状态错误。"""

        provider_name = "failing"
        model = "failing-model"
        streaming = True

        def synthesize_delta(self, text: str) -> bytes:
            """首段文本进入 TTS 时抛出真实现场同类错误。"""

            raise RuntimeError("speech synthesizer has not been started.")

        def drain_audio(self, wait_seconds: float = 0.0) -> bytes:
            """失败场景没有可 drain 音频。"""

            return b""

        def finish(self) -> bytes:
            """失败场景不应依赖 finish。"""

            return b""

        def metrics(self) -> dict:
            """返回固定格式，保证 stream 可以先被打开。"""

            return {"provider": self.provider_name, "model": self.model, "sample_rate_hz": 24000}

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), tts_provider="mock"))
    connection = Connection("dev-speaker")
    register_speaker(app, connection)
    app.output_service.router._injected_tts = FailingTTS()

    try:
        app.output_service.on_assistant_vision_delta(
            AssistantTextDelta(user_id="user-001", session_id="sess-failing-tts", text="会失败")
        )
    except RuntimeError as exc:
        assert "speech synthesizer has not been started" in str(exc)
    else:  # pragma: no cover - 防止输出失败被静默吞掉
        raise AssertionError("expected TTS failure")

    app.output_service.router._injected_tts = MockStreamingTTS(sample_rate_hz=24000)
    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-001", session_id="sess-after-failure", text="恢复播放")
    )

    decisions = [
        json.loads(line)
        for line in (tmp_path / "runs" / "user-001" / "sess-after-failure" / "playback-decisions.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert decisions[-1]["action"] == "play_now"
    assert decisions[-1]["reason"] == "no_active_playback"
    assert connection.chunks


def test_dashscope_tts_missing_key_falls_back_or_fails_explicitly(monkeypatch) -> None:
    """测试目标：验证真实 TTS provider 缺少 key 时的降级和禁用 fallback 行为。

    测试方法：清空 `DASHSCOPE_API_KEY`，分别构建允许 fallback 和禁止 fallback 的配置。
    预期结果：允许 fallback 时返回 mock 和降级原因；禁止 fallback 时抛出明确错误。
    """

    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    provider, reason = build_tts_provider(TtsProviderConfig(provider="dashscope", allow_mock_fallback=True))
    assert provider.provider_name == "mock"
    assert reason and "DASHSCOPE_API_KEY" in reason

    try:
        build_tts_provider(TtsProviderConfig(provider="dashscope", allow_mock_fallback=False))
    except RuntimeError as exc:
        assert "DASHSCOPE_API_KEY" in str(exc)
    else:  # pragma: no cover - 防止禁止 fallback 时静默降级
        raise AssertionError("expected RuntimeError when fallback is disabled")


def test_native_audio_delta_done_closes_stream_with_declared_sample_rate(tmp_path) -> None:
    """测试目标：验证原生 audio delta 走 stream，并在 done 后关闭。

    测试方法：提交 24k PCM 原生音频和 final done。
    预期结果：端侧收到 24k chunk，并收到 output close 事件。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    connection = Connection("dev-native")
    register_speaker(app, connection)
    fmt = StreamFormat(codec="pcm16le", sample_rate=24000, channels=1, chunk_ms=20)

    app.output_service.on_assistant_audio_delta(
        user_id="user-001",
        session_id="sess-native",
        audio=b"\x01\x00" * 480,
        format=fmt,
        metadata={"provider": "fake"},
    )
    app.output_service.on_assistant_audio_delta(
        user_id="user-001",
        session_id="sess-native",
        audio=b"",
        format=fmt,
        final=True,
        metadata={"provider": "fake"},
    )

    assert connection.chunks[0].sample_rate == 24000
    assert any(event.event_name == "stream.output.finish.requested" for event in connection.events)
