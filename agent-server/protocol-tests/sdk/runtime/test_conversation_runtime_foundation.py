from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import realtime_agent.agent_core.vision as vision_module
from realtime_agent.agent_core.providers import AsrProviderConfig, TranscriptEvent
from realtime_agent.audio_pipeline.service import AudioPipeline
from realtime_agent.asset import AssetStoreABC
from realtime_agent.capability import McpGatewayABC, SkillGatewayABC, TaskEngineABC, ToolGatewayABC
from realtime_agent.conversation import (
    AgentCoreABC,
    AgentOutputDelta,
    AgentMemoryABC,
    AgentSnapshot,
    ConversationMemoryService,
    ConversationRuntimeConfig,
    SpeechInputDelta,
    TaskSignal,
)
from realtime_agent.conversation.core import ConversationContext
from realtime_agent.conversation.events import ConversationRuntimeEventEmitter
from realtime_agent.conversation.input import (
    AsrSpeechInputBoundary,
    AsrVoiceActivityBoundary,
    AudioInputBoundary,
    CallbackVisualInputBoundary,
    TurnVisualInputBoundary,
)
from realtime_agent.conversation.recorder import output_delta_record, speech_delta_record
from realtime_agent.conversation.output import ConversationOutputController, ConversationOutputDeltaBridge
from realtime_agent.conversation.core.loop import OmniRealtimeLoop, VlAgentLoop
from realtime_agent.conversation.providers import ASRProviderABC, OmniRealtimeProviderABC, TTSProviderABC, VLMProviderABC
from realtime_agent.conversation.turn import OutputInterruptionController, RealtimeTurnController
from realtime_agent.observability import RunRecorder
from realtime_agent.output import SpeakerSinkABC
from realtime_agent.protocol import StreamChunk, StreamFormat
from realtime_agent.transport import ControlTransportABC, DeviceSession, StreamTransportABC


ROOT = Path(__file__).resolve().parents[4]
CONVERSATION_ROOT = ROOT / "agent-server/realtime_agent/conversation"


def test_conversation_package_keeps_memory_import_compatible(tmp_path) -> None:
    """测试目标：验证 `realtime_agent.conversation` 包化后保留旧记忆服务导入。

    测试方法：从新 package 入口导入 `ConversationMemoryService` 并写入一条消息。
    预期结果：旧导入路径可用，消息仍落入原有 `messages.jsonl`。
    """

    service = ConversationMemoryService(tmp_path / "runs")
    service.append_message(user_id="user-a", device_id="dev-a", message={"role": "user", "content": "你好"})

    assert service.legacy_messages_path(user_id="user-a", device_id="dev-a").exists()


def test_conversation_memory_service_satisfies_agent_memory_contract(tmp_path) -> None:
    """测试目标：验证现有会话记忆服务符合 AgentMemoryABC 最小契约。

    测试方法：按协议类型使用 `ConversationMemoryService`，追加并读取一条用户消息。
    预期结果：服务暴露 append/load 方法，能够返回模型可见 active messages。
    """

    memory: AgentMemoryABC = ConversationMemoryService(tmp_path / "runs")
    memory.append_message(user_id="user-a", device_id="dev-a", message={"role": "user", "content": "你好"})

    messages = memory.load_active_messages(user_id="user-a", device_id="dev-a")

    assert messages[-1]["content"] == "你好"


def test_conversation_runtime_config_defaults_to_legacy() -> None:
    """测试目标：确认 Phase 0 新 runtime 配置默认保护旧链路。

    测试方法：直接构造 `ConversationRuntimeConfig`。
    预期结果：默认 runtime 是 `legacy`。
    """

    assert ConversationRuntimeConfig().runtime == "legacy"


def test_conversation_context_carries_agent_context_fields() -> None:
    """测试目标：验证 ConversationContext 能表达 AgentContext 关键字段。

    测试方法：构造包含 active streams、当前 turn、工具 schema、记忆摘要和 recorder
    的上下文对象。
    预期结果：这些字段可被 AgentCore/AgentLoop 稳定读取，不再只能放进 metadata。
    """

    recorder = object()
    context = ConversationContext(
        user_id="user-a",
        session_id="session-a",
        mode="vision",
        device_id="dev-a",
        active_streams={"mic": "stream-in", "speaker": "stream-out"},
        system_prompt="你是助手",
        current_turn={"final_text": "你好"},
        tool_schemas=({"name": "capture_photo"},),
        memory_summary="用户偏好中文",
        recorder=recorder,
    )

    assert context.active_streams["mic"] == "stream-in"
    assert context.current_turn["final_text"] == "你好"
    assert context.tool_schemas[0]["name"] == "capture_photo"
    assert context.recorder is recorder


def test_agent_core_abc_exposes_design_level_methods() -> None:
    """测试目标：验证 AgentCoreABC 覆盖抽象架构设计文档中的核心接口。

    测试方法：检查 Protocol 上的 `open/consume_input/consume_task_signal/interrupt/close/snapshot`
    方法，并构造 `TaskSignal` 与 `AgentSnapshot`。
    预期结果：AgentCore 抽象可以表达任务信号回流和状态快照，不再只有旧 runtime
    兼容入口。
    """

    for method_name in ("open", "consume_input", "consume_task_signal", "interrupt", "close", "snapshot"):
        assert hasattr(AgentCoreABC, method_name)
    signal = TaskSignal(kind="task.progress", session_id="session-a", user_id="user-a", payload={"step": 1})
    snapshot = AgentSnapshot(user_id="user-a", session_id="session-a", mode="vision", state="thinking")

    assert signal.payload["step"] == 1
    assert snapshot.state == "thinking"


def test_provider_abcs_are_importable_design_contracts() -> None:
    """测试目标：验证四类 provider adapter 抽象已落成代码契约。

    测试方法：从 conversation providers 包导入 VLM、Omni realtime、ASR 和 TTS
    provider ABC。
    预期结果：这些抽象可作为后续 provider adapter 的稳定类型入口。
    """

    assert VLMProviderABC is not None
    assert OmniRealtimeProviderABC is not None
    assert ASRProviderABC is not None
    assert TTSProviderABC is not None
    for method_name in ("generate", "stream_messages"):
        assert hasattr(VLMProviderABC, method_name)
    for method_name in ("open", "append_audio", "append_image", "commit_input", "create_response", "cancel", "close"):
        assert hasattr(OmniRealtimeProviderABC, method_name)
    for method_name in ("append_audio", "cancel", "close"):
        assert hasattr(ASRProviderABC, method_name)
    for method_name in ("synthesize_text", "stream_synthesize"):
        assert hasattr(TTSProviderABC, method_name)


def test_transport_abstractions_are_importable_design_contracts() -> None:
    """测试目标：验证 Transport Layer 抽象已落成代码契约。

    测试方法：导入控制通道、数据流通道和设备会话抽象，并构造一个
    `DeviceSession`。
    预期结果：抽象可导入，设备会话能表达 active streams 和 capabilities。
    """

    session = DeviceSession(
        user_id="user-a",
        device_id="dev-a",
        session_id="session-a",
        active_streams={"mic": "stream-in"},
        capabilities={"sensors": ["mic"]},
    )

    assert ControlTransportABC is not None
    assert StreamTransportABC is not None
    for method_name in ("receive_chunk", "open_stream", "send_chunk", "close_stream", "cancel_stream"):
        assert hasattr(StreamTransportABC, method_name)
    assert session.active_streams["mic"] == "stream-in"
    assert session.capabilities["sensors"] == ["mic"]


def test_audio_input_boundary_abc_is_importable_design_contract() -> None:
    """测试目标：验证 AudioInputBoundary 抽象已落成代码契约。

    测试方法：从 conversation input 包导入 `AudioInputBoundary`。
    预期结果：Input Layer 可以表达音频规范化边界，而不是只有语音边界。
    """

    assert AudioInputBoundary is not None


def test_audio_pipeline_satisfies_audio_input_boundary_contract() -> None:
    """测试目标：验证真实 AudioPipeline 可以作为 AudioInputBoundary 使用。

    测试方法：构造无 agent_core 的 `AudioPipeline`，直接调用 `normalize()`。
    预期结果：返回规范化后的 `StreamChunk`，不会调用 Agent 或触发输出。
    """

    boundary: AudioInputBoundary = AudioPipeline(agent_core=object())
    chunk = _mic_chunk(seq=4, payload=b"\x01\x00" * 320)

    normalized = boundary.normalize(chunk)

    assert normalized.stream_type == "sensor.mic"
    assert normalized.sample_rate == 16000
    assert normalized.channels == 1


def test_asset_store_abc_is_importable_design_contract() -> None:
    """测试目标：验证 AssetStoreABC 已落成代码契约。

    测试方法：从 asset 包导入 `AssetStoreABC`。
    预期结果：输入层和能力层可以使用该抽象引用资产存储能力。
    """

    assert AssetStoreABC is not None
    for method_name in ("put", "latest", "read", "claim", "source_map"):
        assert hasattr(AssetStoreABC, method_name)


def test_capability_gateway_abcs_are_importable_design_contracts() -> None:
    """测试目标：验证 Capability Layer gateway 抽象已落成代码契约。

    测试方法：导入 Tool、Task、Skill、MCP 四类 gateway ABC。
    预期结果：Agent 层后续可通过这些抽象访问能力层，而不是直接依赖业务实现。
    """

    assert ToolGatewayABC is not None
    assert TaskEngineABC is not None
    assert SkillGatewayABC is not None
    assert McpGatewayABC is not None
    for method_name in ("tool_schemas", "provider_schemas", "call_tool"):
        assert hasattr(ToolGatewayABC, method_name)
    for method_name in ("start_task", "cancel_task", "query_task", "handle_command_result", "emit_signal"):
        assert hasattr(TaskEngineABC, method_name)


def test_speaker_sink_abc_is_importable_design_contract() -> None:
    """测试目标：验证 Output Layer speaker sink 抽象已落成代码契约。

    测试方法：从 output 包导入 `SpeakerSinkABC`。
    预期结果：输出层可以用该抽象表达最终下行音频写入端。
    """

    assert SpeakerSinkABC is not None


def test_conversation_runtime_does_not_import_legacy_realtime_pipeline() -> None:
    """测试目标：验证 conversation runtime 不依赖旧 realtime pipeline 包装层。

    测试方法：扫描 `realtime_agent/conversation` 源码，检查是否导入
    `realtime_agent.realtime_pipeline`。
    预期结果：conversation 可以复用 OutputService 等基础服务，但不能反向依赖
    legacy pipeline helper。
    """

    source = "\n".join(path.read_text(encoding="utf-8") for path in CONVERSATION_ROOT.rglob("*.py"))

    assert "realtime_agent.realtime_pipeline" not in source


def test_speech_input_delta_record_does_not_embed_audio_bytes() -> None:
    """测试目标：验证语音输入增量记录不会把音频 bytes 写入事件 JSON。

    测试方法：构造包含音频 payload 的 `SpeechInputDelta` 并转换为记录结构。
    预期结果：记录只包含音频 seq，不包含原始 payload。
    """

    chunk = StreamChunk(
        stream_id="stream-a",
        stream_type="sensor.mic",
        user_id="user-a",
        session_id="session-a",
        seq=7,
        payload=b"\x01\x02\x03\x04",
        codec="pcm16le",
        sample_rate=16000,
        channels=1,
    )
    delta = SpeechInputDelta(kind="audio_chunk", session_id="session-a", user_id="user-a", stream_id="stream-a", audio=chunk)

    record = speech_delta_record(delta)

    assert record["kind"] == "audio_chunk"
    assert record["audio_seq"] == 7
    assert "payload" not in record
    assert b"\x01\x02" not in record.values()


def test_agent_output_delta_record_does_not_embed_audio_bytes() -> None:
    """测试目标：验证 Agent 输出增量记录不会把原生音频 bytes 写入事件 JSON。

    测试方法：构造包含原生音频的 `AgentOutputDelta` 并转换为记录结构。
    预期结果：记录只包含音频长度和采样率，不包含原始音频。
    """

    delta = AgentOutputDelta(kind="audio_chunk", session_id="session-a", output_id="out-a", audio=b"\x01\x02\x03", sample_rate_hz=24000)

    record = output_delta_record(delta)

    assert record["kind"] == "audio_chunk"
    assert record["audio_bytes"] == 3
    assert record["sample_rate_hz"] == 24000
    assert "audio" not in record
    assert b"\x01\x02\x03" not in record.values()


def test_asr_speech_input_boundary_maps_sentence_events_to_turn_deltas(tmp_path, monkeypatch) -> None:
    """测试目标：验证 ASR-backed 输入边界把句子事件映射为统一 SpeechInputDelta。

    测试方法：替换 ASR provider，让一片音频返回 sentence_begin、partial 和
    sentence_end/final。
    预期结果：输出顺序为 `audio_chunk`、`turn_started`、`asr_text_delta`、
    `turn_ended(final_text)`，并保留 ASR 诊断字段。
    """

    class SentenceAsrProvider:
        provider_name = "sentence-asr"
        model = "sentence-asr"

        def append_audio(self, chunk: StreamChunk) -> list[TranscriptEvent]:
            return [
                TranscriptEvent(text="", sentence_id=12, sentence_begin=True, begin_time_ms=100),
                TranscriptEvent(text="你是谁", sentence_id=12),
                TranscriptEvent(text="你是谁", final=True, sentence_id=12, sentence_end=True, end_time_ms=520),
            ]

        def cancel(self) -> None:
            pass

    monkeypatch.setattr(vision_module, "build_asr_provider", lambda config: (SentenceAsrProvider(), None))
    boundary = AsrSpeechInputBoundary(
        config=AsrProviderConfig(provider="sentence-asr", model="sentence-asr"),
        recorder=RunRecorder(tmp_path / "runs"),
    )

    deltas = list(boundary.append_audio(_mic_chunk(seq=1, payload=b"\x01\x00" * 320)))

    assert [delta.kind for delta in deltas] == ["audio_chunk", "turn_started", "asr_text_delta", "turn_ended"]
    assert deltas[1].metadata["asr_boundary"] == "sentence_begin"
    assert deltas[2].text_delta == "你是谁"
    assert deltas[3].final_text == "你是谁"
    assert deltas[3].metadata["asr_boundary"] == "sentence_end"


def test_asr_voice_activity_boundary_outputs_only_speech_boundaries() -> None:
    """测试目标：验证 ASR/VAD 合一 provider 的句边界先统一成 speech boundary。

    测试方法：直接向 `AsrVoiceActivityBoundary` 输入 sentence_begin 和
    sentence_end 事件。
    预期结果：组件只输出 `speech_started/speech_stopped`，不输出 ASR 文本、打断
    或模型提交语义。
    """

    boundary = AsrVoiceActivityBoundary()
    chunk = _mic_chunk(seq=1, payload=b"\x01\x00" * 320)

    started = boundary.append_asr_event(
        chunk=chunk,
        event=TranscriptEvent(text="", sentence_id=9, sentence_begin=True, begin_time_ms=10),
    )
    stopped = boundary.append_asr_event(
        chunk=chunk,
        event=TranscriptEvent(text="你好", sentence_id=9, sentence_end=True, final=True, end_time_ms=300),
    )

    assert [item.kind for item in started + stopped] == ["speech_started", "speech_stopped"]
    assert all("interrupt" not in item.metadata for item in started + stopped)
    assert all("commit" not in item.metadata for item in started + stopped)
    assert all("text" not in item.metadata for item in started + stopped)


def test_output_interruption_controller_uses_shared_rules() -> None:
    """测试目标：验证输出打断判断集中在共享控制器。

    测试方法：分别模拟活跃 output stream、可打断生成状态和空闲状态。
    预期结果：活跃输出或 thinking/speaking/tool_running 状态会触发打断，空闲状态
    不触发。
    """

    active_stream = {"value": None}
    state = {"value": ""}
    controller = OutputInterruptionController(
        active_output_stream_id=lambda user_id, session_id: active_stream["value"],
        state=lambda user_id, session_id: state["value"],
    )

    assert not controller.evaluate(user_id="user-a", session_id="session-a").should_interrupt

    active_stream["value"] = "out-1"
    assert controller.evaluate(user_id="user-a", session_id="session-a").should_interrupt

    active_stream["value"] = None
    state["value"] = "tool_running"
    assert controller.evaluate(user_id="user-a", session_id="session-a").should_interrupt


def test_realtime_turn_controller_emits_shared_speech_and_cancel_events(tmp_path) -> None:
    """测试目标：验证 turn controller 统一处理 speech started 和取消请求。

    测试方法：构造带活跃 output stream 的打断控制器，输入一个 `turn_started`。
    预期结果：事件顺序为 `speech_started`、`output_cancel_requested`，并调用链路
    专属 started 回调。
    """

    emitter = ConversationRuntimeEventEmitter(recorder=RunRecorder(tmp_path / "runs"))
    callbacks = []
    visual_callbacks = []
    controller = RealtimeTurnController(
        emitter=emitter,
        interruption_controller=OutputInterruptionController(
            active_output_stream_id=lambda user_id, session_id: "out-1",
            state=lambda user_id, session_id: "speaking",
        ),
        stream_id_for_session=lambda session_id: "stream-a",
        visual_boundary=CallbackVisualInputBoundary(
            on_started=lambda **kwargs: visual_callbacks.append(kwargs),
        ),
    )
    delta = SpeechInputDelta(
        kind="turn_started",
        session_id="session-a",
        user_id="user-a",
        metadata={"speech_boundary": "speech_started"},
    )

    controller.handle_turn_started(delta, reason="test_speech_started", on_started=lambda context: callbacks.append(context))

    assert [event.event for event in emitter.events()] == ["speech_started", "output_cancel_requested"]
    assert callbacks[0].stream_id == "stream-a"
    assert visual_callbacks[0]["stream_id"] == "stream-a"
    assert emitter.events()[1].payload["output_stream_id"] == "out-1"
    assert (tmp_path / "runs" / "user-a" / "session-a" / "conversation-events.jsonl").exists()
    assert (tmp_path / "runs" / "user-a" / "session-a" / "agent-events.jsonl").exists()


def test_realtime_turn_controller_ignores_server_vad_echo_at_output_start(tmp_path) -> None:
    """测试目标：验证助手输出刚开始后的 server VAD 回采不会打断当前播放。

    测试方法：构造活跃 output stream，并把 `turn_started` 标记为
    `server_vad_speech_started`；随后输入同一 stream 的 `turn_ended`。
    预期结果：controller 只记录 ignored 事件，不触发 started/stopped 回调，也不输出
    `output_cancel_requested`，避免回采尾随触发空 turn 提交。
    """

    now = {"value": 1000}
    emitter = ConversationRuntimeEventEmitter(recorder=RunRecorder(tmp_path / "runs"))
    callbacks = []
    visual_callbacks = []
    controller = RealtimeTurnController(
        emitter=emitter,
        interruption_controller=OutputInterruptionController(
            active_output_stream_id=lambda user_id, session_id: "out-1",
            state=lambda user_id, session_id: "speaking",
            now_ms=lambda: now["value"],
        ),
        stream_id_for_session=lambda session_id: "stream-mic",
        visual_boundary=CallbackVisualInputBoundary(
            on_started=lambda **kwargs: visual_callbacks.append(("started", kwargs)),
            on_ended=lambda **kwargs: visual_callbacks.append(("stopped", kwargs)),
        ),
    )
    started = SpeechInputDelta(
        kind="turn_started",
        session_id="session-a",
        user_id="user-a",
        stream_id="stream-mic",
        metadata={"speech_boundary": "speech_started", "rms": 900},
    )
    stopped = SpeechInputDelta(
        kind="turn_ended",
        session_id="session-a",
        user_id="user-a",
        stream_id="stream-mic",
        metadata={"speech_boundary": "speech_stopped"},
    )

    controller.observe_active_output(user_id="user-a", session_id="session-a")
    started_context = controller.handle_turn_started(
        started,
        reason="server_vad_speech_started",
        on_started=lambda context: callbacks.append(context),
    )
    stopped_context = controller.handle_turn_ended(
        stopped,
        reason="server_vad_speech_stopped",
        on_ended=lambda context: callbacks.append(context),
    )

    assert started_context.ignored is True
    assert stopped_context.ignored is True
    assert callbacks == []
    assert visual_callbacks == []
    assert [event.event for event in emitter.events()] == ["speech_started_ignored", "speech_stopped_ignored"]
    assert emitter.events()[0].payload["turn_ignored"] is True
    assert emitter.events()[0].payload["ignore_reason"] == "assistant_output_echo_guard"


def test_turn_visual_input_boundary_tracks_active_turn() -> None:
    """测试目标：验证 VisualInputBoundary 持有 turn 生命周期状态。

    测试方法：调用 `turn_started()` 后查询 active turn，再调用 `turn_ended()`。
    预期结果：开始后能读到当前视觉 turn，结束后状态被清理。
    """

    boundary = TurnVisualInputBoundary()

    boundary.turn_started(
        user_id="user-a",
        session_id="session-a",
        stream_id="stream-mic",
        reason="speech_started",
        diagnostics={"rms": 100},
    )
    active = boundary.active_turn(session_id="session-a")
    boundary.turn_ended(
        user_id="user-a",
        session_id="session-a",
        stream_id="stream-mic",
        reason="speech_stopped",
        diagnostics={},
    )

    assert active is not None
    assert active.stream_id == "stream-mic"
    assert active.diagnostics["rms"] == 100
    assert boundary.active_turn(session_id="session-a") is None


def test_conversation_output_controller_routes_agent_output_delta() -> None:
    """测试目标：验证输出适配层使用 AgentOutputRouter。

    测试方法：构造假的 OutputService 和 recorder，分别发送旧 kind 和设计文档中的
    `text/audio/control/task_signal` 输出增量。
    预期结果：文本进入 `submit_text`，音频进入 `submit_audio`，取消进入
    `interrupt_user`，TaskSignal 可按策略转文本输出，且每个 delta 都会记录
    conversation event。
    """

    class FakeOutputService:
        def __init__(self) -> None:
            self.calls = []
            self.audio_listeners = []
            self.finish_listeners = []

        def add_output_audio_delta_listener(self, listener) -> None:
            self.audio_listeners.append(listener)

        def add_output_finished_listener(self, listener) -> None:
            self.finish_listeners.append(listener)

        def submit_text(self, **kwargs) -> None:
            self.calls.append(("text", kwargs))

        def submit_audio(self, **kwargs) -> None:
            self.calls.append(("audio", kwargs))

        def interrupt_user(self, user_id: str, *, session_id: str, reason: str) -> None:
            self.calls.append(("cancel", {"user_id": user_id, "session_id": session_id, "reason": reason}))

    class FakeRecorder:
        def __init__(self) -> None:
            self.records = []

        def record_conversation_event(self, session_id: str, record: dict) -> None:
            self.records.append((session_id, record))

    output_service = FakeOutputService()
    recorder = FakeRecorder()
    controller = ConversationOutputController(output_service=output_service, recorder=recorder)

    controller.emit(AgentOutputDelta(kind="text_delta", session_id="session-a", text_delta="你好", metadata={"user_id": "user-a"}))
    controller.emit(
        AgentOutputDelta(
            kind="audio_chunk",
            session_id="session-a",
            audio=b"\x00\x00",
            sample_rate_hz=24000,
            metadata={"user_id": "user-a"},
        )
    )
    controller.emit(
        AgentOutputDelta(
            kind="output_cancel_requested",
            session_id="session-a",
            metadata={"user_id": "user-a", "reason": "test_cancel"},
        )
    )
    controller.emit(AgentOutputDelta(kind="text", session_id="session-a", payload="继续说", metadata={"user_id": "user-a"}))
    controller.emit(
        AgentOutputDelta(
            kind="audio",
            session_id="session-a",
            payload=b"\x01\x02",
            metadata={"user_id": "user-a", "sample_rate_hz": 16000},
        )
    )
    controller.emit(
        AgentOutputDelta(
            kind="control",
            session_id="session-a",
            payload={"action": "cancel_output", "reason": "control_cancel"},
            metadata={"user_id": "user-a"},
        )
    )
    controller.emit(
        AgentOutputDelta(
            kind="task_signal",
            session_id="session-a",
            payload={"message": "后台任务完成"},
            metadata={"user_id": "user-a"},
        )
    )

    assert [name for name, _payload in output_service.calls] == ["text", "audio", "cancel", "text", "audio", "cancel", "text"]
    assert output_service.calls[0][1]["text"] == "你好"
    assert output_service.calls[1][1]["format"].sample_rate == 24000
    assert output_service.calls[2][1]["reason"] == "test_cancel"
    assert output_service.calls[3][1]["text"] == "继续说"
    assert output_service.calls[4][1]["format"].sample_rate == 16000
    assert output_service.calls[5][1]["reason"] == "control_cancel"
    assert output_service.calls[6][1]["text"] == "后台任务完成"
    assert [record[1]["event"] for record in recorder.records] == ["conversation.output_delta"] * 7


def test_conversation_output_delta_bridge_records_output_service_events() -> None:
    """测试目标：验证旧 OutputService 输出事件会桥接成 AgentOutputDelta 记录。

    测试方法：构造假的 OutputService，注册 bridge 后手动触发 audio delta 和 finish
    listener。
    预期结果：recorder 收到两条 `conversation.agent_output_delta`，分别表示音频片
    和输出完成。
    """

    class FakeOutputService:
        def __init__(self) -> None:
            self.audio_listeners = []
            self.finish_listeners = []

        def add_output_audio_delta_listener(self, listener) -> None:
            self.audio_listeners.append(listener)

        def add_output_finished_listener(self, listener) -> None:
            self.finish_listeners.append(listener)

    class FakeRecorder:
        def __init__(self) -> None:
            self.records = []

        def record_conversation_event(self, session_id: str, record: dict) -> None:
            self.records.append((session_id, record))

    output_service = FakeOutputService()
    recorder = FakeRecorder()
    bridge = ConversationOutputDeltaBridge(output_service=output_service, recorder=recorder)

    bridge.bind()
    output_service.audio_listeners[0](
        {
            "event": "assistant_audio.delta",
            "user_id": "user-a",
            "session_id": "session-a",
            "stream_id": "stream-out",
            "payload_size": 640,
            "chunk_count": 2,
            "stream_format": {"sample_rate": 24000},
            "tts": {"audio_bytes": 640},
        }
    )
    output_service.finish_listeners[0]("user-a", "session-a", "stream-out")

    assert [record[1]["event"] for record in recorder.records] == ["conversation.agent_output_delta", "conversation.agent_output_delta"]
    assert recorder.records[0][1]["kind"] == "audio_chunk"
    assert recorder.records[0][1]["sample_rate_hz"] == 24000
    assert recorder.records[0][1]["payload_size"] == 640
    assert recorder.records[1][1]["kind"] == "output_finished"


def test_omni_realtime_loop_commits_and_creates_response() -> None:
    """测试目标：验证 Omni provider loop 由 AgentLoop 适配器承接。

    测试方法：构造假的 Omni core，向 `OmniRealtimeLoop` 输入 audio chunk 和
    `turn_ended`。
    预期结果：音频 append、commit、input committed 标记和 create_response 都由
    loop 触发。
    """

    class FakeOmniCore:
        def __init__(self) -> None:
            self.calls = []

        def append_audio_event(self, chunk: StreamChunk) -> None:
            self.calls.append(("audio", chunk.seq))

        def commit_input(self, user_id: str, session_id: str, *, reason: str) -> None:
            self.calls.append(("commit", user_id, session_id, reason))

        def on_conversation_input_committed(self, *, session_id: str, reason: str) -> None:
            self.calls.append(("committed", session_id, reason))

        def create_response(self, user_id: str, session_id: str, *, reason: str) -> None:
            self.calls.append(("response", user_id, session_id, reason))

    core = FakeOmniCore()
    loop = OmniRealtimeLoop(core=core)

    loop.consume_input(SpeechInputDelta(kind="audio_chunk", session_id="session-a", user_id="user-a", audio=_mic_chunk(seq=1, payload=b"\x01\x00" * 320)))
    loop.consume_input(SpeechInputDelta(kind="turn_ended", session_id="session-a", user_id="user-a"))

    assert [call[0] for call in core.calls] == ["audio", "commit", "committed", "response"]


def test_omni_realtime_loop_owns_provider_callbacks() -> None:
    """测试目标：验证 Omni conversation provider callbacks 由 AgentLoop 组装。

    测试方法：构造假的 Omni core，通过 `OmniRealtimeLoop.provider_callbacks()`
    触发 audio、done、provider event、tool delta、tool done 和 error 回调。
    预期结果：所有回调都进入 core 的底层 handler，但 callback factory 的 owner 是
    `OmniRealtimeLoop`，后续 core.open 可注入该 factory。
    """

    class FakeOmniCore:
        def __init__(self) -> None:
            self.calls = []

        def _handle_provider_audio_delta(self, *, user_id: str, session_id: str, audio: bytes, format, metadata: dict) -> None:
            self.calls.append(("audio_delta", user_id, session_id, audio, format.sample_rate, metadata))

        def _handle_provider_audio_done(self, *, user_id: str, session_id: str, metadata: dict) -> None:
            self.calls.append(("audio_done", user_id, session_id, metadata))

        def _record_provider_event(self, *, user_id: str, session_id: str, record: dict) -> None:
            self.calls.append(("provider_event", user_id, session_id, record))

        def _mark_session_failed(self, *, user_id: str, session_id: str, message: str, record: dict) -> None:
            self.calls.append(("error", user_id, session_id, message, record))

        def _handle_provider_tool_call_delta(self, record: dict) -> None:
            self.calls.append(("tool_delta", record))

        def _handle_provider_tool_call_done(self, *, user_id: str, session_id: str, record: dict) -> dict:
            self.calls.append(("tool_done", user_id, session_id, record))
            return {"tool_call_id": record["tool_call_id"], "ok": True}

        def _replay_audio_for_tool_result(self, *, session_id: str, result: dict) -> list[bytes]:
            self.calls.append(("replay_audio", session_id, result))
            return [b"pcm"]

    core = FakeOmniCore()
    loop = OmniRealtimeLoop(core=core)
    callbacks = loop.provider_callbacks(user_id="user-a", session_id="session-a")

    callbacks.audio_delta(b"\x01\x02", StreamFormat(codec="pcm16le", sample_rate=24000, channels=1), {"event": "delta"})
    callbacks.audio_done({"event": "done"})
    callbacks.provider_event({"event": "raw"})
    callbacks.tool_call_delta({"tool_call_id": "call-a", "arguments_delta": "{}"})
    result = callbacks.tool_call_done({"tool_call_id": "call-a"})
    replay = callbacks.replay_audio_for_tool_result(result)
    callbacks.error("provider error", {"event": "error"})

    assert [call[0] for call in core.calls] == [
        "audio_delta",
        "audio_done",
        "provider_event",
        "tool_delta",
        "tool_done",
        "replay_audio",
        "error",
    ]
    assert result["ok"] is True
    assert replay == [b"pcm"]


def test_vl_agent_loop_handles_asr_text_and_final_text() -> None:
    """测试目标：验证 VL provider loop 由 AgentLoop 适配器承接。

    测试方法：构造假的 Vision core，依次输入 audio chunk、ASR 文本增量和
    `turn_ended(final_text)`。
    预期结果：loop 缓存音频、同步 ASR delta，并由 `VlAgentLoop` 自己完成
    started/completed 事件、provider 文本释放和 assistant 消息写入。
    """

    class FakeRecorder:
        def __init__(self) -> None:
            self.model_requests = []
            self.agent_events = []
            self.timeline = []

        def record_model_request(self, session_id: str, record: dict) -> None:
            self.model_requests.append((session_id, record))

        def record_agent_event(self, session_id: str, event: dict) -> None:
            self.agent_events.append((session_id, event))

        def record_timeline_checkpoint(self, session_id: str, *, checkpoint: str, user_id: str, fields: dict, stream_id: str | None = None) -> None:
            self.timeline.append((session_id, checkpoint, user_id, fields, stream_id))

    class FakeContextCompiler:
        def compile(self, request) -> SimpleNamespace:
            return SimpleNamespace(
                messages=[{"role": "user", "content": request.current_input["transcript"]}],
                tools=[],
                instructions=request.base_instructions,
                provider=request.provider,
                model=request.model,
                warnings=[],
                truncations=[],
                notifications=[],
                metadata={},
                mode=request.mode,
                context_sources=[],
                prompt_records=lambda: [],
                source_records=lambda: [],
            )

    class FakeVisualAppender:
        def flush_turn_assets(self, context) -> SimpleNamespace:
            return SimpleNamespace(messages=[], source_records=[], events=[])

    class FakeControlService:
        def __init__(self) -> None:
            self.messages = []
            self.events = []

        def append_message(self, user_id: str, message: dict) -> None:
            self.messages.append((user_id, message))

        def publish(self, event) -> None:
            self.events.append(event)

    class FakeVisionCore:
        def __init__(self) -> None:
            self._latest_audio_chunk_by_session = {}
            self._responded_input_streams = set()
            self._cancelled_users = set()
            self._interruption_reason_by_user = {}
            self._interrupted_generation_reason_by_user = {}
            self._response_generation_by_user = {}
            self.calls = []
            self.recorder = FakeRecorder()
            self.control_service = FakeControlService()
            self.context_compiler = FakeContextCompiler()
            self.visual_appender = FakeVisualAppender()
            self.vision_model = SimpleNamespace(provider_name="fake-vlm", model="fake-model", prompt="系统提示")
            self.memory_service = None
            self.tool_gateway = None
            self.output_service = object()
            self.asset_service = None
            self.max_context_messages = 8
            self.RECOVERABLE_ERROR_MESSAGE = "出错了"
            self.outputs = []
            self.states = []
            self.assistant_parts = []
            self.errors = []

        def on_conversation_asr_text_delta(self, user_id: str, session_id: str, *, stream_id: str, text: str, diagnostics: dict) -> None:
            self.calls.append(("asr", user_id, session_id, stream_id, text, diagnostics))

        def _record_event(self, event: str, **payload) -> None:
            self.calls.append(("event", event, payload))

        def _should_ignore_transcript_as_echo(self, *, chunk: StreamChunk, transcript: str) -> bool:
            return False

        def _stop_visual_sampler(self, **kwargs) -> None:
            self.calls.append(("stop_visual", kwargs))

        def _set_turn_state(self, user_id: str, session_id: str, state: str, *, reason: str) -> None:
            self.states.append((user_id, session_id, state, reason))

        def _mark_user_activity(self, user_id: str, session_id: str) -> None:
            self.calls.append(("activity", user_id, session_id))

        @staticmethod
        def _turn_key(*, chunk: StreamChunk, transcript: str) -> str:
            return f"{chunk.stream_id}:{chunk.seq}:{transcript}"

        def _record_duplicate_turn(self, **kwargs) -> None:
            self.calls.append(("duplicate", kwargs))

        def _next_response_generation(self, user_id: str) -> int:
            generation = self._response_generation_by_user.get(user_id, 0) + 1
            self._response_generation_by_user[user_id] = generation
            return generation

        def _maybe_capture_visual_frame_before_response(self, *, user_id: str, session_id: str) -> None:
            self.calls.append(("capture_visual", user_id, session_id))

        @staticmethod
        def _model_request_messages(messages: list[dict]) -> list[dict]:
            return list(messages)

        def _stream_model(self, *, messages: list[dict], transcript: str, tools: list[dict]):
            yield "回复。"

        @staticmethod
        def _response_cancel_reason(user_id: str, generation: int) -> str | None:
            return None

        @staticmethod
        def _extract_vision_delta(item) -> str:
            return str(item)

        def _remember_assistant_parts(self, *, user_id: str, generation: int, parts: list[str]) -> None:
            self.assistant_parts.extend(parts)

        def _emit_assistant_vision_delta(self, *, user_id: str, session_id: str, text: str, generation: int) -> bool:
            self.outputs.append((user_id, session_id, text, generation))
            return True

        def _handle_response_error(self, **kwargs) -> None:
            self.errors.append(kwargs.get("error"))
            self.calls.append(("response_error", kwargs))

        def _emit_output_best_effort(self, **kwargs) -> bool:
            self.outputs.append((kwargs["user_id"], kwargs["session_id"], kwargs["text"], kwargs.get("generation")))
            return True

        @staticmethod
        def _generation_finalized_reason(user_id: str, generation: int) -> str | None:
            return None

        def _cleanup_generation(self, user_id: str, generation: int) -> None:
            self.calls.append(("cleanup", user_id, generation))

        def _extend_assistant_output_guard(self, user_id: str, *, start_ms, tail_ms: int) -> None:
            self.calls.append(("output_guard", user_id, start_ms, tail_ms))

    core = FakeVisionCore()
    loop = VlAgentLoop(core=core, stream_id_for_session=lambda session_id: "stream-a")
    chunk = _mic_chunk(seq=3, payload=b"\x01\x00" * 320, final=True)

    loop.consume_input(SpeechInputDelta(kind="audio_chunk", session_id="session-a", user_id="user-a", audio=chunk))
    loop.consume_input(SpeechInputDelta(kind="asr_text_delta", session_id="session-a", user_id="user-a", text_delta="你好"))
    loop.consume_input(SpeechInputDelta(kind="turn_ended", session_id="session-a", user_id="user-a", final_text="你好"))

    assert core._latest_audio_chunk_by_session["session-a"] is chunk
    assert core.calls[0][0] == "asr"
    assert core.calls[1][0] == "event"
    assert core.calls[1][1] == "vision.conversation_final_text.received"
    assert [event.event_name for event in core.control_service.events] == ["agent.response.started", "agent.response.completed"]
    assert core.errors == []
    assert core.outputs == [("user-a", "session-a", "回复。", 1), ("user-a", "session-a", "", 1)]
    assert core.control_service.messages[-1][1]["role"] == "assistant"
    assert core.control_service.messages[-1][1]["content"] == "回复。"
    assert core.recorder.model_requests[0][1]["runner"] == "vl_agent_loop"


def _mic_chunk(*, seq: int, payload: bytes, stream_id: str = "stream-a", final: bool = False) -> StreamChunk:
    """构造测试用麦克风音频 chunk。"""

    return StreamChunk(
        user_id="user-a",
        session_id="session-a",
        stream_id=stream_id,
        stream_type="sensor.mic",
        seq=seq,
        payload=payload,
        codec="pcm16le",
        sample_rate=16000,
        channels=1,
        duration_ms=20,
        final=final,
    )
