import json
import threading
import time

import pytest

from realtime_agent.agent_core.base import AgentCoreEvent
from realtime_agent.agent_core.providers import OpenAICompatibleVisionModelAdapter, TranscriptEvent
from realtime_agent.agent_core.router import AgentCoreRouter
from realtime_agent.agent_core.vision import VisionRealtimeAgentCore
from realtime_agent.app import RealtimeAgentApp, RealtimeAgentConfig
from realtime_agent.output import AssistantTextDelta
from realtime_agent.output.service import OutputItem
from realtime_agent.protocol import Event, StreamChunk
from realtime_agent.realtime_pipeline import (
    OmniAgentCore,
    OmniInputBoundary,
    OmniRealtimePipeline,
    OmniResponseEngine,
    PipelineEventEmitter,
    RealtimeAudioNormalizer,
    RealtimeOutputController,
    VisionInputBoundary,
    VisionRealtimePipeline,
    VisionResponseEngine,
)
from realtime_agent.tasks import BaseTask, TaskSignal
from realtime_agent.tools import BaseTool, ToolContext, ToolResult


pytestmark = pytest.mark.sdk


def test_agent_mode_text_builds_text_core(tmp_path) -> None:
    """测试目标：验证 `agent.mode=vision` 构建 VisionRealtimePipeline。

    测试方法：用 text 模式创建 RealtimeAgentApp。
    预期结果：app 正常初始化，外层是 VisionRealtimePipeline，内部保留 VisionRealtimeAgentCore。
    """
    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))

    assert isinstance(app.agent_core, VisionRealtimePipeline)
    assert isinstance(app.agent_core.core, VisionRealtimeAgentCore)
    assert hasattr(app.agent_core, "append_audio_event")


def test_agent_mode_omni_audio_builds_realtime_core(tmp_path) -> None:
    """测试目标：验证 `agent.mode=omni` 能创建 OmniRealtimePipeline。

    测试方法：用 `agent_mode=omni` 创建 RealtimeAgentApp。
    预期结果：app 正常初始化，不在构造阶段连接真实 provider。
    """
    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="omni"))

    assert isinstance(app.agent_core, OmniRealtimePipeline)
    assert isinstance(app.agent_core.core, OmniAgentCore)


def test_agent_mode_auto_defaults_to_text_for_now(tmp_path) -> None:
    """测试目标：验证 `agent.mode=auto` 当前保守落到Vision 链路。

    测试方法：用 auto 模式创建 RealtimeAgentApp。
    预期结果：返回 VisionRealtimePipeline；文档中声明后续再接端侧能力判断。
    """
    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="auto"))

    assert isinstance(app.agent_core, VisionRealtimePipeline)
    assert isinstance(app.agent_core.core, VisionRealtimeAgentCore)


def test_vision_realtime_pipeline_exposes_real_sequence_components(tmp_path) -> None:
    """测试目标：验证 Text realtime 时序图中的核心组件都有真实代码对象。

    测试方法：创建 text 模式应用，检查 pipeline 内部组件类型，并手动驱动
    session/upstream/downstream 生命周期入口。
    预期结果：组件不是概念占位，且生命周期入口会输出稳定的 pipeline 事件。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    pipeline = app.agent_core

    assert isinstance(pipeline, VisionRealtimePipeline)
    assert isinstance(pipeline.core, VisionRealtimeAgentCore)
    assert isinstance(pipeline.normalizer, RealtimeAudioNormalizer)
    assert isinstance(pipeline.input_boundary, VisionInputBoundary)
    assert isinstance(pipeline.response_engine, VisionResponseEngine)
    assert isinstance(pipeline.output_controller, RealtimeOutputController)
    assert isinstance(pipeline.emitter, PipelineEventEmitter)

    pipeline.open("user-pipeline", "session-pipeline")
    pipeline.on_audio_input_opened(
        user_id="user-pipeline",
        session_id="session-pipeline",
        stream_id="stream-in-pipeline",
    )
    pipeline.on_downstream_opened(
        user_id="user-pipeline",
        session_id="session-pipeline",
        stream_id="stream-out-pipeline",
    )
    pipeline.core.on_speech_started(
        "user-pipeline",
        "session-pipeline",
        stream_id="stream-in-pipeline",
        reason="paraformer_sentence_begin",
        diagnostics={"sentence_id": 1},
    )
    pipeline.core.on_speech_stopped(
        "user-pipeline",
        "session-pipeline",
        stream_id="stream-in-pipeline",
        reason="paraformer_sentence_end",
        diagnostics={"sentence_id": 1},
    )

    emitted_names = [event.event for event in pipeline.emitter.events()]
    assert "response_engine_ready" in emitted_names
    assert "session_ready" in emitted_names
    assert "upstream_ready" in emitted_names
    assert "downstream_ready" in emitted_names
    assert "speech_started" in emitted_names
    assert "speech_stopped" in emitted_names


def test_omni_realtime_pipeline_exposes_real_sequence_components(tmp_path) -> None:
    """测试目标：验证 Omni realtime 时序图中的核心组件都有真实代码对象。

    测试方法：创建 mock omni 应用，检查 pipeline 内部组件类型，并手动驱动
    session/upstream/downstream 生命周期和 provider speech 事件。
    预期结果：组件不是概念占位，且 provider 原始事件会转换成稳定 pipeline 事件。
    """

    app = RealtimeAgentApp(
        RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="omni", omni_provider="mock")
    )
    pipeline = app.agent_core

    assert isinstance(pipeline, OmniRealtimePipeline)
    assert isinstance(pipeline.core, OmniAgentCore)
    assert isinstance(pipeline.normalizer, RealtimeAudioNormalizer)
    assert isinstance(pipeline.input_boundary, OmniInputBoundary)
    assert isinstance(pipeline.response_engine, OmniResponseEngine)
    assert isinstance(pipeline.output_controller, RealtimeOutputController)
    assert isinstance(pipeline.emitter, PipelineEventEmitter)

    pipeline.open("user-pipeline", "session-pipeline")
    pipeline.on_audio_input_opened(
        user_id="user-pipeline",
        session_id="session-pipeline",
        stream_id="stream-in-pipeline",
    )
    pipeline.on_downstream_opened(
        user_id="user-pipeline",
        session_id="session-pipeline",
        stream_id="stream-out-pipeline",
    )
    pipeline.core._record_provider_event(
        user_id="user-pipeline",
        session_id="session-pipeline",
        record={"event": "omni.input_audio_buffer.speech_started", "provider": "mock"},
    )
    pipeline.core._record_provider_event(
        user_id="user-pipeline",
        session_id="session-pipeline",
        record={"event": "omni.input_audio_buffer.speech_stopped", "provider": "mock"},
    )

    emitted_names = [event.event for event in pipeline.emitter.events()]
    assert "response_engine_ready" in emitted_names
    assert "session_ready" in emitted_names
    assert "upstream_ready" in emitted_names
    assert "downstream_ready" in emitted_names
    assert "speech_started" in emitted_names
    assert "speech_stopped" in emitted_names


def test_agent_mode_custom_fails_fast(tmp_path) -> None:
    """测试目标：验证 custom 模式没有 app-module 工厂时明确失败。

    测试方法：用 custom 模式创建 RealtimeAgentApp。
    预期结果：抛出 NotImplementedError。
    """
    with pytest.raises(NotImplementedError, match="custom"):
        RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="custom"))


class CustomCore:
    """测试用自定义 Agent Core。

    主要功能：实现最小公共接口，证明 router 可以注入业务自定义 core。
    主要属性：`opened` 记录打开过的会话。
    """

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.opened: list[tuple[str, str]] = []
        self._events: list[AgentCoreEvent] = []

    def open(self, user_id: str, session_id: str) -> None:
        """记录打开会话。"""
        self.opened.append((user_id, session_id))

    def append_audio_event(self, chunk: StreamChunk) -> None:
        """记录音频输入事件。"""
        self._events.append(AgentCoreEvent("custom.audio", user_id=chunk.user_id, session_id=chunk.session_id))

    def commit_input(self, user_id: str, session_id: str, *, reason: str = "endpoint_commit") -> None:
        """记录输入提交。"""
        self._events.append(AgentCoreEvent("custom.commit", user_id=user_id, session_id=session_id, payload={"reason": reason}))

    def interrupt(self, user_id: str, *, reason: str) -> None:
        """记录取消。"""
        self._events.append(AgentCoreEvent("custom.interrupt", user_id=user_id, payload={"reason": reason}))

    def close(self, user_id: str, *, reason: str) -> None:
        """记录关闭。"""
        self._events.append(AgentCoreEvent("custom.close", user_id=user_id, payload={"reason": reason}))

    def events(self) -> list[AgentCoreEvent]:
        """返回事件快照。"""
        return list(self._events)


def test_agent_core_router_supports_custom_factory() -> None:
    """测试目标：验证 AgentCoreRouter 支持 custom factory 扩展点。

    测试方法：直接调用 `build()` 并传入 `custom_factories`。
    预期结果：返回自定义 core，且依赖参数能透传给 factory。
    """

    built = AgentCoreRouter.build(
        mode="custom",
        custom_factories={"custom": lambda **kwargs: CustomCore(**kwargs)},
        sentinel="ok",
    )

    assert isinstance(built, CustomCore)
    assert built.kwargs["sentinel"] == "ok"


def test_vision_agent_core_exposes_unified_lifecycle_events(tmp_path) -> None:
    """测试目标：验证 VisionRealtimeAgentCore 实现统一 AgentCore 生命周期接口。

    测试方法：创建 text app 后调用 open、commit_input、interrupt、close。
    预期结果：`events()` 返回对应统一事件名。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))

    app.agent_core.open("user-text", "sess-text")
    app.agent_core.commit_input("user-text", "sess-text", reason="unit_commit")
    app.agent_core.interrupt("user-text", reason="unit_interrupt")
    app.agent_core.close("user-text", reason="unit_close")

    names = [event.event for event in app.agent_core.events()]
    assert "session.opened" in names
    assert "input.committed" in names
    assert "response.cancelled" in names
    assert "session.closed" in names


class WeatherTool(BaseTool):
    """测试用天气 Tool。"""

    name = "lookup_weather"
    description = "查询天气"
    progress_message = "正在查询天气"

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """测试目标：验证 Tool 只能通过 ToolContext 执行。

        测试方法：返回入参和当前用户上下文。
        预期结果：VisionRealtimeAgentCore 可通过 ToolGateway 取得该结果并回填模型消息。
        """

        return ToolResult.success(
            data={"city": input_data["city"], "user_id": context.user_id},
            message="weather ready",
        )


class ToolCallingVisionModel:
    provider_name = "mock-tool"
    model = "mock-tool-model"

    def __init__(self) -> None:
        self.calls = 0

    def stream_messages(self, *, messages: list[dict], tools: list[dict]):
        self.calls += 1
        if self.calls == 1:
            assert any(item["function"]["name"] == "lookup_weather" for item in tools)
            yield {
                "type": "tool_call",
                "id": "call-weather-1",
                "name": "lookup_weather",
                "arguments": {"city": "shanghai"},
            }
            return
        assistant_message = next(item for item in messages if item["role"] == "assistant" and item.get("tool_calls"))
        provider_call = assistant_message["tool_calls"][0]
        assert provider_call["type"] == "function"
        assert provider_call["id"] == "call-weather-1"
        assert provider_call["function"]["name"] == "lookup_weather"
        assert json.loads(provider_call["function"]["arguments"]) == {"city": "shanghai"}
        tool_message = next(item for item in messages if item["role"] == "tool")
        assert "name" not in tool_message
        assert json.loads(tool_message["content"])["data"]["city"] == "shanghai"
        yield "上海天气已查询。"

    def stream_text(self, transcript: str):
        yield "unused"

    def cancel(self) -> None:
        pass


class ToolLoopFailingVisionModel:
    """测试用Vision 模型：工具调用后的第二轮 provider 请求抛出异常。"""

    provider_name = "mock-tool-failing"
    model = "mock-tool-failing-model"

    def __init__(self) -> None:
        self.calls = 0

    def stream_messages(self, *, messages: list[dict], tools: list[dict]):
        """首轮返回工具调用，第二轮模拟 provider 内部异常。"""

        self.calls += 1
        if self.calls == 1:
            yield {
                "type": "tool_call",
                "id": "call-weather-fail",
                "name": "lookup_weather",
                "arguments": {"city": "shanghai"},
            }
            return
        raise RuntimeError("provider rejected tool call history")

    def stream_text(self, transcript: str):
        yield "unused"

    def cancel(self) -> None:
        pass


class RecoveryVisionModel:
    """测试用Vision 模型：异常后一轮新输入可以继续正常回复。"""

    provider_name = "mock-recovery"
    model = "mock-recovery-model"

    def stream_messages(self, *, messages: list[dict], tools: list[dict]):
        """直接返回恢复后的文本。"""

        yield "恢复后的回答。"

    def stream_text(self, transcript: str):
        yield "unused"

    def cancel(self) -> None:
        pass


class MultiDeltaRecoveryVisionModel:
    """测试用Vision 模型：把同一条回复拆成多个文本 delta。"""

    provider_name = "mock-multi-delta"
    model = "mock-multi-delta-model"

    def stream_messages(self, *, messages: list[dict], tools: list[dict]):
        """返回两段文本，模拟真实模型分片输出。"""

        yield "第一段，"
        yield "第二段。"

    def stream_text(self, transcript: str):
        yield "unused"

    def cancel(self) -> None:
        pass


class UnpunctuatedDeltaVisionModel:
    """测试用Vision 模型：首个 delta 没有停顿标点。"""

    provider_name = "mock-unpunctuated-delta"
    model = "mock-unpunctuated-delta-model"

    def stream_messages(self, *, messages: list[dict], tools: list[dict]):
        """逐字返回文本，用于验证 Vision 链路不等待标点或完整回复。"""

        yield "你"
        yield "好"
        yield "。"

    def stream_text(self, transcript: str):
        yield "unused"

    def cancel(self) -> None:
        pass


class InterruptingVisionModel:
    """测试用Vision 模型：首段文本后触发用户打断。"""

    provider_name = "mock-interrupting"
    model = "mock-interrupting-model"

    def __init__(self, on_after_first_delta) -> None:
        self.on_after_first_delta = on_after_first_delta
        self.cancelled = False

    def stream_messages(self, *, messages: list[dict], tools: list[dict]):
        """先返回可播文本，再模拟端侧上报打断。"""

        yield "正在回答，"
        self.on_after_first_delta()
        yield "这段不应该继续播出。"

    def stream_text(self, transcript: str):
        yield "unused"

    def cancel(self) -> None:
        self.cancelled = True


class FailingOutputAdapter:
    """测试用输出适配器：模拟 TTS/output 状态损坏。"""

    def emit_vision_delta(self, *, user_id: str, session_id: str, text: str, final: bool = False) -> None:
        """始终抛出输出异常。"""

        raise RuntimeError("fallback output failed")


class RecordingOutputAdapter:
    """测试用输出适配器：记录 Vision 链路写出的Vision 分片。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def emit_vision_delta(self, *, user_id: str, session_id: str, text: str, final: bool = False) -> None:
        """记录输出参数，便于断言打断后不会继续 flush。"""

        self.calls.append({"user_id": user_id, "session_id": session_id, "text": text, "final": final})


class ImmediateFinalAsrProvider:
    """测试用 ASR provider，收到任意音频立即返回 final。"""

    provider_name = "immediate-final-asr"
    model = "immediate-final-asr"

    def __init__(self, text: str) -> None:
        self.text = text

    def append_audio(self, chunk: StreamChunk) -> list[TranscriptEvent]:
        """返回固定 final 文本。"""

        return [TranscriptEvent(text=self.text, final=True)]

    def cancel(self) -> None:
        """测试 provider 无需释放资源。"""


class BlockingVisionModel:
    """测试用Vision 模型，阻塞直到测试放行。"""

    provider_name = "blocking-text"
    model = "blocking-text-model"

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.cancelled = False

    def stream_messages(self, *, messages: list[dict], tools: list[dict]):
        """等待测试放行后返回文本。"""

        self.started.set()
        self.release.wait(timeout=2)
        yield "后台回复完成。"

    def stream_text(self, transcript: str):
        yield from self.stream_messages(messages=[{"role": "user", "content": transcript}], tools=[])

    def cancel(self) -> None:
        self.cancelled = True


class SupersededBlockingVisionModel:
    """测试用Vision 模型：首轮阻塞，第二轮立即返回。"""

    provider_name = "superseded-blocking-text"
    model = "superseded-blocking-text-model"

    def __init__(self) -> None:
        self.first_started = threading.Event()
        self.first_release = threading.Event()
        self.calls = 0

    def stream_messages(self, *, messages: list[dict], tools: list[dict]):
        """首轮等待测试放行，第二轮直接返回文本。"""

        self.calls += 1
        if self.calls == 1:
            self.first_started.set()
            self.first_release.wait(timeout=2)
            yield "旧回复不应该输出。"
            return
        yield "新回复应该输出。"

    def stream_text(self, transcript: str):
        yield from self.stream_messages(messages=[{"role": "user", "content": transcript}], tools=[])

    def cancel(self) -> None:
        """测试模型不需要真实取消。"""


class ReleasedThenBlockingVisionModel:
    """测试用Vision 模型：先释放一段文本，再阻塞等待打断。"""

    provider_name = "released-then-blocking-text"
    model = "released-then-blocking-text-model"

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def stream_messages(self, *, messages: list[dict], tools: list[dict]):
        """先输出可听见片段，再等待测试放行后尝试输出旧片段。"""

        yield "这段已经说出口。"
        self.started.set()
        self.release.wait(timeout=2)
        yield "这段取消后不应该出现。"

    def stream_text(self, transcript: str):
        """兼容旧 Vision model provider 接口。"""

        yield from self.stream_messages(messages=[{"role": "user", "content": transcript}], tools=[])

    def cancel(self) -> None:
        """测试模型不需要真实取消。"""


class CancelRecordingAsrPipeline:
    """测试用 ASR 代理：记录 Text 打断是否误取消正在收音的 ASR。"""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.cancelled = False

    def append_audio(self, chunk: StreamChunk):
        """转发音频分片到真实测试 ASR。"""

        return self.inner.append_audio(chunk)

    def cancel(self) -> None:
        """记录取消调用，并继续转发给真实 ASR。"""

        self.cancelled = True
        self.inner.cancel()


class CloseRecordingStream:
    """测试用模型流对象：记录底层流是否被关闭。"""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        """记录 close 调用。"""

        self.closed = True


class StreamingFailCompleteSucceedTTS:
    """测试用 TTS：流式首包失败，但完整文本合成可用。"""

    provider_name = "streaming-fail-complete-ok"
    model = "streaming-fail-complete-ok-model"
    streaming = True

    def __init__(self) -> None:
        self.streamed_texts: list[str] = []
        self.completed_texts: list[str] = []

    def synthesize_delta(self, text: str) -> bytes:
        """模拟 DashScope streaming_call 首段抛出 InvalidTask。"""

        self.streamed_texts.append(text)
        raise RuntimeError("speech synthesizer has not been started.")

    def synthesize_text(self, text: str) -> bytes:
        """完整文本合成成功，作为流式失败后的补播路径。"""

        self.completed_texts.append(text)
        return b"\x03\x00" * 960

    def drain_audio(self, wait_seconds: float = 0.0) -> bytes:
        """失败后没有后台音频。"""

        return b""

    def finish(self) -> bytes:
        """本测试不依赖 streaming finish。"""

        return b""

    def metrics(self) -> dict:
        """返回固定音频格式。"""

        return {"provider": self.provider_name, "model": self.model, "sample_rate_hz": 24000}


class FirstDeltaFailsThenStreamsTTS:
    """测试用 TTS：首段流式失败，后续流式如果被调用会返回音频。"""

    provider_name = "first-delta-fails"
    model = "first-delta-fails-model"
    streaming = True

    def __init__(self) -> None:
        self.streamed_texts: list[str] = []
        self.completed_texts: list[str] = []

    def synthesize_delta(self, text: str) -> bytes:
        """第一段抛错，后续调用会成功，用于捕获重复播放风险。"""

        self.streamed_texts.append(text)
        if len(self.streamed_texts) == 1:
            raise RuntimeError("speech synthesizer has not been started.")
        return b"\x04\x00" * 480

    def synthesize_text(self, text: str) -> bytes:
        """完整文本补播路径。"""

        self.completed_texts.append(text)
        return b"\x05\x00" * 960

    def drain_audio(self, wait_seconds: float = 0.0) -> bytes:
        """本测试不依赖后台 drain。"""

        return b""

    def finish(self) -> bytes:
        """本测试不依赖 streaming finish。"""

        return b""

    def metrics(self) -> dict:
        """返回固定音频格式。"""

        return {"provider": self.provider_name, "model": self.model, "sample_rate_hz": 24000}


class CaptureHistoryVisionModel:
    """测试用Vision 模型，记录 VisionRealtimeAgentCore 传入的运行时 messages。"""

    provider_name = "mock-history"
    model = "mock-history-model"

    def __init__(self) -> None:
        self.prompt = ""
        self.messages = []

    def stream_messages(self, *, messages: list[dict], tools: list[dict]):
        """记录 messages 并返回固定回复。"""

        self.messages.append(list(messages))
        yield "历史已加载。"

    def stream_text(self, transcript: str):
        yield "unused"

    def cancel(self) -> None:
        pass


class FixedSummaryAgent:
    """测试用会话摘要器。"""

    def summarize(self, *, previous_summary: str, messages: list[dict]) -> str:
        """返回包含旧消息线索的固定摘要。"""

        return "当前对话状态：\n- 压缩前消息 0 已被归档。"


def test_vision_agent_core_calls_tool_gateway_and_continues_model_loop(tmp_path) -> None:
    """测试目标：验证 VisionRealtimeAgentCore 通过 ToolGateway 调用 Tool 并继续模型循环。

    测试方法：注入会产生一次 tool_call 的 mock Vision 模型和测试 Tool，发送 final mic chunk。
    预期结果：工具结果写入消息历史，模型第二轮生成最终回复，tool trace 被记录。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    app.tool_registry.register(WeatherTool())
    app.agent_core.vision_model = ToolCallingVisionModel()
    session_id = "sess-tool-loop"

    app.agent_core.append_audio_event(
        StreamChunk(
            user_id="user-tool",
            session_id=session_id,
            stream_id="stream-mic",
            stream_type="sensor.mic",
            seq=0,
            payload=b"hello",
            final=True,
        )
    )

    session_dir = tmp_path / "runs" / "user-tool" / session_id
    message_text = (session_dir / "messages.jsonl").read_text(encoding="utf-8")
    trace_text = (session_dir / "tool-events.jsonl").read_text(encoding="utf-8")
    model_request = (session_dir / "model-request.json").read_text(encoding="utf-8")

    assert "assistant_tool_call.done" in message_text
    assert "tool_result.done" in message_text
    assert "上海天气已查询。" in message_text
    assert "lookup_weather" in trace_text
    assert "你是中文语音助手" in model_request
    assert "lookup_weather" in model_request


def test_vision_agent_core_recovers_after_provider_error_in_tool_loop(tmp_path) -> None:
    """测试目标：验证工具循环中的 provider 异常会反馈给用户且不阻断后续对话。

    测试方法：mock Vision 模型首轮返回工具调用，第二轮抛出 provider 异常；随后替换为
    正常模型并发送另一段 final 麦克风输入。
    预期结果：当前轮写入可恢复错误、中文兜底回复和 system.error；下一轮仍能正常回复。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    app.tool_registry.register(WeatherTool())
    app.agent_core.vision_model = ToolLoopFailingVisionModel()
    user_id = "user-recoverable-error"
    session_id = "sess-recoverable-error"

    app.agent_core.append_audio_event(
        StreamChunk(
            user_id=user_id,
            session_id=session_id,
            stream_id="stream-error-first",
            stream_type="sensor.mic",
            seq=0,
            payload=b"hello",
            final=True,
        )
    )

    session_dir = tmp_path / "runs" / user_id / session_id
    messages_text = (session_dir / "messages.jsonl").read_text(encoding="utf-8")
    events_text = (session_dir / "events.jsonl").read_text(encoding="utf-8")
    agent_events_text = (session_dir / "agent-events.jsonl").read_text(encoding="utf-8")
    assert VisionRealtimeAgentCore.RECOVERABLE_ERROR_MESSAGE in messages_text
    assert "system.error.raised" in events_text
    assert "response.failed" in agent_events_text
    assert "provider rejected tool call history" in agent_events_text

    app.agent_core.vision_model = RecoveryVisionModel()
    app.agent_core.append_audio_event(
        StreamChunk(
            user_id=user_id,
            session_id=session_id,
            stream_id="stream-error-second",
            stream_type="sensor.mic",
            seq=0,
            payload=b"hello again",
            final=True,
        )
    )

    messages_text = (session_dir / "messages.jsonl").read_text(encoding="utf-8")
    assert "恢复后的回答。" in messages_text


def test_vision_agent_recovery_does_not_raise_when_fallback_output_fails(tmp_path) -> None:
    """测试目标：验证恢复提示的 TTS/output 再次失败时不会冒泡到 stream 层。

    测试方法：mock Vision 模型在工具循环第二轮抛异常，同时把 output adapter 替换成
    始终抛异常的实现。
    预期结果：`append_audio_event()` 不抛异常，messages 中仍记录兜底文本，agent
    事件中同时包含 `response.failed` 和 `output.failed`。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    app.tool_registry.register(WeatherTool())
    app.agent_core.vision_model = ToolLoopFailingVisionModel()
    app.agent_core.output_adapter = FailingOutputAdapter()
    user_id = "user-output-recovery"
    session_id = "sess-output-recovery"

    app.agent_core.append_audio_event(
        StreamChunk(
            user_id=user_id,
            session_id=session_id,
            stream_id="stream-output-fail",
            stream_type="sensor.mic",
            seq=0,
            payload=b"hello",
            final=True,
        )
    )

    session_dir = tmp_path / "runs" / user_id / session_id
    messages_text = (session_dir / "messages.jsonl").read_text(encoding="utf-8")
    agent_events_text = (session_dir / "agent-events.jsonl").read_text(encoding="utf-8")
    assert VisionRealtimeAgentCore.RECOVERABLE_ERROR_MESSAGE in messages_text
    assert "response.failed" in agent_events_text
    assert "output.failed" in agent_events_text
    assert "fallback output failed" in agent_events_text


def test_vision_agent_streaming_tts_failure_reopens_stream_and_keeps_real_answer(tmp_path) -> None:
    """测试目标：验证普通回答的流式 TTS 首包失败后会重开 streaming TTS。

    测试方法：mock Vision 模型返回真实回答，注入一个首段流式失败、第二次流式成功的
    provider。
    预期结果：消息历史保存真实回答，agent 事件记录 `output.tts_stream_reopen`，
    不使用全文本 TTS 补播，并生成音频。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    app.agent_core.vision_model = RecoveryVisionModel()
    tts = FirstDeltaFailsThenStreamsTTS()
    app.output_service.router._injected_tts = tts
    user_id = "user-streaming-tts-recovery"
    session_id = "sess-streaming-tts-recovery"

    app.agent_core.append_audio_event(
        StreamChunk(
            user_id=user_id,
            session_id=session_id,
            stream_id="stream-tts-recovery",
            stream_type="sensor.mic",
            seq=0,
            payload=b"hello",
            final=True,
        )
    )

    session_dir = tmp_path / "runs" / user_id / session_id
    messages_text = (session_dir / "messages.jsonl").read_text(encoding="utf-8")
    agent_events_text = (session_dir / "agent-events.jsonl").read_text(encoding="utf-8")
    stream_events_text = (session_dir / "stream-events.jsonl").read_text(encoding="utf-8")
    assert "恢复后的回答。" in messages_text
    assert VisionRealtimeAgentCore.RECOVERABLE_ERROR_MESSAGE not in messages_text
    assert "response.failed" not in agent_events_text
    assert "output.tts_stream_reopen" in agent_events_text
    assert "output.recovered" not in agent_events_text
    assert "stream.failed" in stream_events_text
    assert tts.streamed_texts == ["恢复后的回答。", "恢复后的回答。"]
    assert tts.completed_texts == []
    assert list((session_dir / "audio").glob("output-*.wav"))


def test_vision_agent_continues_streaming_after_reopening_tts_source(tmp_path) -> None:
    """测试目标：验证流式 TTS 失败重开后，本轮后续 delta 继续走 streaming TTS。

    测试方法：模型返回两段文本；TTS 第一段流式失败，第二段流式如果被调用会返回音频。
    预期结果：第一段被重试，第二段继续进入新 streaming source，不使用全文本 TTS。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    app.agent_core.vision_model = MultiDeltaRecoveryVisionModel()
    tts = FirstDeltaFailsThenStreamsTTS()
    app.output_service.router._injected_tts = tts
    user_id = "user-stop-streaming-after-failure"
    session_id = "sess-stop-streaming-after-failure"

    app.agent_core.append_audio_event(
        StreamChunk(
            user_id=user_id,
            session_id=session_id,
            stream_id="stream-stop-after-failure",
            stream_type="sensor.mic",
            seq=0,
            payload=b"hello",
            final=True,
        )
    )

    session_dir = tmp_path / "runs" / user_id / session_id
    messages_text = (session_dir / "messages.jsonl").read_text(encoding="utf-8")
    agent_events_text = (session_dir / "agent-events.jsonl").read_text(encoding="utf-8")
    assert "第一段，第二段。" in messages_text
    assert tts.streamed_texts == ["第一段，", "第一段，", "第二段。"]
    assert tts.completed_texts == []
    assert agent_events_text.count("output.tts_stream_reopen") == 1
    assert "output.recovered" not in agent_events_text
    assert len(list((session_dir / "audio").glob("output-*.wav"))) == 1


def test_vision_agent_records_turn_states_and_releases_each_vision_delta(tmp_path) -> None:
    """测试目标：验证 Vision 链路记录状态机，并逐 delta 释放Vision 到 TTS。

    测试方法：模型返回两段文本 delta。
    预期结果：runs 中记录 transcribing/thinking/speaking 完整状态，且两段文本均被释放。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    app.agent_core.vision_model = MultiDeltaRecoveryVisionModel()
    user_id = "user-text-state"
    session_id = "sess-text-state"

    app.agent_core.append_audio_event(
        StreamChunk(
            user_id=user_id,
            session_id=session_id,
            stream_id="stream-text-state",
            stream_type="sensor.mic",
            seq=0,
            payload=b"hello",
            final=True,
        )
    )

    session_dir = tmp_path / "runs" / user_id / session_id
    agent_events_text = (session_dir / "agent-events.jsonl").read_text(encoding="utf-8")
    assert "agent.turn_state.changed" in agent_events_text
    assert '"agent_core": "VisionRealtimeAgentCore"' in agent_events_text
    assert '"modality": "vision"' in agent_events_text
    assert '"state": "transcribing"' in agent_events_text
    assert '"state": "thinking"' in agent_events_text
    assert '"state": "completed"' in agent_events_text
    assert "vision_delta_realtime" in agent_events_text
    assert agent_events_text.count("vision.response_gate.released") == 2


def test_vision_agent_releases_first_vision_delta_without_waiting_for_punctuation(tmp_path) -> None:
    """测试目标：验证第一个无标点 text delta 到达后立刻进入 TTS。

    测试方法：模型逐字返回“你”“好”“。”，首个 delta 没有任何自然停顿标点。
    预期结果：runs 中记录三次实时释放，说明 Text gate 没有等待标点、长度阈值或完整回复。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    app.agent_core.vision_model = UnpunctuatedDeltaVisionModel()
    user_id = "user-vision-realtime-delta"
    session_id = "sess-vision-realtime-delta"

    app.agent_core.append_audio_event(
        StreamChunk(
            user_id=user_id,
            session_id=session_id,
            stream_id="stream-vision-realtime-delta",
            stream_type="sensor.mic",
            seq=0,
            payload=b"hello",
            final=True,
        )
    )

    session_dir = tmp_path / "runs" / user_id / session_id
    messages_text = (session_dir / "messages.jsonl").read_text(encoding="utf-8")
    agent_events_text = (session_dir / "agent-events.jsonl").read_text(encoding="utf-8")
    assert "你好。" in messages_text
    assert agent_events_text.count("vision.response_gate.released") == 3
    assert agent_events_text.count('"reason": "vision_delta_realtime"') == 3


def test_vision_agent_interrupt_keeps_only_released_assistant_text(tmp_path) -> None:
    """测试目标：验证用户打断时只保存已经释放给用户的助手文本。

    测试方法：模型首段文本释放后触发 `interrupt()`，随后尝试继续输出第二段文本。
    预期结果：第二段不会进入消息历史；assistant 消息带 interrupted 元数据。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    user_id = "user-text-interrupt"
    session_id = "sess-text-interrupt"
    app.agent_core.open(user_id, session_id)
    app.agent_core.output_adapter = RecordingOutputAdapter()
    app.agent_core.asr_pipeline = CancelRecordingAsrPipeline(app.agent_core.asr_pipeline)
    app.agent_core.vision_model = InterruptingVisionModel(
        on_after_first_delta=lambda: app.agent_core.interrupt(user_id, reason="unit_interrupt")
    )

    app.agent_core.append_audio_event(
        StreamChunk(
            user_id=user_id,
            session_id=session_id,
            stream_id="stream-text-interrupt",
            stream_type="sensor.mic",
            seq=0,
            payload=b"hello",
            final=True,
        )
    )

    session_dir = tmp_path / "runs" / user_id / session_id
    messages_text = (session_dir / "messages.jsonl").read_text(encoding="utf-8")
    agent_events_text = (session_dir / "agent-events.jsonl").read_text(encoding="utf-8")
    assert "正在回答，" in messages_text
    assert "这段不应该继续播出。" not in messages_text
    assert '"interrupted": true' in messages_text
    assert '"interrupted_reason": "unit_interrupt"' in messages_text
    assert "response.interrupted" in agent_events_text
    assert '"state": "interrupted"' in agent_events_text
    assert app.agent_core.vision_model.cancelled
    assert not app.agent_core.asr_pipeline.cancelled
    assert {"user_id": user_id, "session_id": session_id, "text": "", "final": True} not in app.agent_core.output_adapter.calls


def test_openai_compatible_vision_model_cancel_closes_active_stream() -> None:
    """测试目标：验证 Vision provider 取消时会关闭底层流式连接。

    测试方法：绕过网络构造 adapter，注入带 close 方法的 active stream 后调用 cancel。
    预期结果：cancel 标记置位，底层 stream 的 close 被调用，避免打断后等待 provider 超时。
    """

    adapter = object.__new__(OpenAICompatibleVisionModelAdapter)
    adapter._cancelled = False
    adapter._stream_lock = threading.RLock()
    stream = CloseRecordingStream()
    adapter._active_stream = stream

    adapter.cancel()

    assert adapter._cancelled
    assert stream.closed


def test_vision_agent_asr_delta_does_not_cancel_active_output(tmp_path) -> None:
    """测试目标：验证 Vision 链路不再用 ASR partial 文本触发插话。

    测试方法：先手动让 OutputService 打开一条未 final 的 speaker 输出，再向
    VisionRealtimeAgentCore 送入一片非 final 麦克风音频，触发 mock ASR delta。
    预期结果：端侧不会收到 output cancel 事件，runs 中也不会记录 ASR 侧 barge-in。
    """

    class Connection:
        """测试连接，保存下发到端侧的事件和音频。"""

        def __init__(self, device_id: str) -> None:
            self.device_id = device_id
            self.events: list[Event] = []
            self.chunks: list[StreamChunk] = []

        def push_event(self, event: Event) -> None:
            """记录控制事件。"""

            self.events.append(event)

        def push_stream_chunk(self, chunk: StreamChunk) -> None:
            """记录音频 chunk。"""

            self.chunks.append(chunk)

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    user_id = "user-text-barge-in"
    session_id = "dev-text-barge-in"
    connection = Connection(session_id)
    app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id=user_id,
            producer_id=session_id,
            payload={
                "device_id": session_id,
                "auth": {"mode": "disabled"},
                "supports": {"sensors": [], "actuators": []},
                "properties": {"realtime_agent.audio_output": "actuator.speaker"},
            },
        ),
        connection,
    )
    app.agent_core.open(user_id, session_id)
    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(
            user_id=user_id,
            session_id=session_id,
            text="这是一段还在播放的回答。",
            intent=OutputItem(user_id=user_id, session_id=session_id, priority="normal"),
        )
    )

    app.agent_core.append_audio_event(
        StreamChunk(
            user_id=user_id,
            session_id=session_id,
            stream_id="stream-text-barge-in",
            stream_type="sensor.mic",
            seq=0,
            payload=b"new speech",
            final=False,
        )
    )

    event_names = [event.event_name for event in connection.events]
    assert "stream.output.cancel.requested" not in event_names
    assert "stream.output.cancelled" not in event_names
    agent_events_text = (tmp_path / "runs" / user_id / session_id / "agent-events.jsonl").read_text(encoding="utf-8")
    assert "text.asr_barge_in.detected" not in agent_events_text
    assert "response.cancelled" not in agent_events_text


def test_vision_agent_server_vad_cancels_active_output(tmp_path) -> None:
    """测试目标：验证 Text realtime 由服务端 VAD 触发插话取消。

    测试方法：启用 `server_only` VAD，先打开一条未完成 speaker 输出，再输入高 RMS
    麦克风音频片。
    预期结果：端侧收到 output cancel 事件，runs 记录 vision.vad.speech_started。
    """

    class Connection:
        """测试连接，保存下发到端侧的事件和音频。"""

        def __init__(self, device_id: str) -> None:
            self.device_id = device_id
            self.events: list[Event] = []
            self.chunks: list[StreamChunk] = []

        def push_event(self, event: Event) -> None:
            """记录控制事件。"""

            self.events.append(event)

        def push_stream_chunk(self, chunk: StreamChunk) -> None:
            """记录音频 chunk。"""

            self.chunks.append(chunk)

    app = RealtimeAgentApp(
        RealtimeAgentConfig(
            runs_root=str(tmp_path / "runs"),
            agent_mode="vision",
            audio_pipeline_vad="server_only",
            audio_pipeline_vad_rms_threshold=96,
            audio_pipeline_vad_silence_timeout_ms=40,
        )
    )
    user_id = "user-text-server-vad"
    session_id = "dev-text-server-vad"
    connection = Connection(session_id)
    app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id=user_id,
            producer_id=session_id,
            payload={
                "device_id": session_id,
                "auth": {"mode": "disabled"},
                "supports": {"sensors": [], "actuators": []},
                "properties": {"realtime_agent.audio_output": "actuator.speaker"},
            },
        ),
        connection,
    )
    app.agent_core.open(user_id, session_id)
    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(
            user_id=user_id,
            session_id=session_id,
            text="这是一段还在播放的回答。",
            intent=OutputItem(user_id=user_id, session_id=session_id, priority="normal"),
        )
    )

    app.audio_pipeline.process(
        StreamChunk(
            user_id=user_id,
            session_id=session_id,
            stream_id="stream-text-server-vad",
            stream_type="sensor.mic",
            seq=0,
            payload=b"\xff\x7f" * 320,
            final=False,
            duration_ms=20,
        )
    )

    event_names = [event.event_name for event in connection.events]
    assert "audio.speech.started" in event_names
    assert "stream.output.cancel.requested" in event_names
    assert "stream.output.cancelled" in event_names
    agent_events_text = (tmp_path / "runs" / user_id / session_id / "agent-events.jsonl").read_text(encoding="utf-8")
    assert "vision.vad.speech_started" in agent_events_text
    assert "server_vad_speech_started" in agent_events_text


def test_vision_agent_paraformer_sentence_begin_cancels_active_output(tmp_path) -> None:
    """测试目标：验证 Paraformer sentence_begin 能作为 Text realtime 的插话信号。

    测试方法：注入返回 `sentence_begin=True` 且无文本的 ASR provider，并让助手输出
    处于播放状态。
    预期结果：端侧收到 output cancel 事件，runs 记录 `paraformer_sentence_begin`。
    """

    class Connection:
        """测试连接，保存下发到端侧的事件和音频。"""

        def __init__(self, device_id: str) -> None:
            self.device_id = device_id
            self.events: list[Event] = []
            self.chunks: list[StreamChunk] = []

        def push_event(self, event: Event) -> None:
            """记录控制事件。"""

            self.events.append(event)

        def push_stream_chunk(self, chunk: StreamChunk) -> None:
            """记录音频 chunk。"""

            self.chunks.append(chunk)

    class SentenceBeginAsrProvider:
        """测试用 ASR provider，模拟 Paraformer 句子开始事件。"""

        provider_name = "dashscope"
        model = "paraformer-realtime-v2"

        def append_audio(self, chunk: StreamChunk) -> list[TranscriptEvent]:
            """返回一次无文本 sentence_begin 事件。"""

            return [
                TranscriptEvent(
                    text="",
                    final=False,
                    sentence_id=1,
                    sentence_begin=True,
                    begin_time_ms=900,
                )
            ]

        def cancel(self) -> None:
            """测试 provider 无需释放资源。"""

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    user_id = "user-text-paraformer-begin"
    session_id = "dev-text-paraformer-begin"
    stream_id = "stream-text-paraformer-begin"
    connection = Connection(session_id)
    app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id=user_id,
            producer_id=session_id,
            payload={
                "device_id": session_id,
                "auth": {"mode": "disabled"},
                "supports": {"sensors": [], "actuators": []},
                "properties": {"realtime_agent.audio_output": "actuator.speaker"},
            },
        ),
        connection,
    )
    app.agent_core.open(user_id, session_id)
    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(
            user_id=user_id,
            session_id=session_id,
            text="这是一段还在播放的回答。",
            intent=OutputItem(user_id=user_id, session_id=session_id, priority="normal"),
        )
    )
    app.agent_core.asr_pipeline._providers[stream_id] = SentenceBeginAsrProvider()

    app.agent_core.append_audio_event(
        StreamChunk(
            user_id=user_id,
            session_id=session_id,
            stream_id=stream_id,
            stream_type="sensor.mic",
            seq=0,
            payload=b"\x01\x00" * 320,
            final=False,
        )
    )

    event_names = [event.event_name for event in connection.events]
    assert event_names.count("audio.speech.started") == 1
    assert "stream.output.cancel.requested" in event_names
    assert "stream.output.cancelled" in event_names
    agent_events_text = (tmp_path / "runs" / user_id / session_id / "agent-events.jsonl").read_text(encoding="utf-8")
    assert "vision.vad.speech_started" in agent_events_text
    assert "paraformer_sentence_begin" in agent_events_text


def test_vision_pipeline_emits_output_audio_events_and_honors_pause_resume(tmp_path) -> None:
    """测试目标：验证 Text 输出音频通过 PipelineEvent 桥接，并受下行水位控制。

    测试方法：注册浏览器眼镜连接，暂停下行后提交一段 Text delta，再恢复下行。
    预期结果：暂停期间不写端侧音频；恢复后写出音频，并记录 `pipeline.output_audio_delta`。
    """

    class Connection:
        """测试连接，保存端侧事件和音频分片。"""

        def __init__(self, device_id: str) -> None:
            self.device_id = device_id
            self.events: list[Event] = []
            self.chunks: list[StreamChunk] = []

        def push_event(self, event: Event) -> None:
            """记录控制事件。"""

            self.events.append(event)

        def push_stream_chunk(self, chunk: StreamChunk) -> None:
            """记录下行音频。"""

            self.chunks.append(chunk)

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    user_id = "user-text-pause"
    session_id = "dev-text-pause"
    connection = Connection(session_id)
    app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id=user_id,
            producer_id=session_id,
            payload={
                "device_id": session_id,
                "auth": {"mode": "disabled"},
                "supports": {"sensors": [], "actuators": []},
                "properties": {"realtime_agent.audio_output": "actuator.speaker"},
            },
        ),
        connection,
    )
    app.agent_core.open(user_id, session_id)
    app.agent_core.on_downstream_opened(
        user_id=user_id,
        session_id=session_id,
        stream_id=f"{session_id}:downstream",
        stream_type="actuator.speaker",
    )

    app.agent_core.pause_downstream(user_id, session_id)
    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id=user_id, session_id=session_id, text="暂停期间先不要写出。", generation_id=1)
    )
    assert connection.chunks == []

    app.agent_core.resume_downstream(user_id, session_id)
    assert connection.chunks
    agent_events_text = (tmp_path / "runs" / user_id / session_id / "agent-events.jsonl").read_text(encoding="utf-8")
    assert "pipeline.output_audio_delta" in agent_events_text
    assert "assistant_audio.delta_buffered_while_paused" in agent_events_text


def test_vision_agent_ignores_asr_final_inside_assistant_output_guard(tmp_path) -> None:
    """测试目标：验证助手播放期间捕获到的 ASR final 不会触发自问自答。

    测试方法：手动设置 VisionRealtimeAgentCore 的助手输出保护窗，并注入立即返回 final 的测试
    ASR provider；送入时间戳落在保护窗内的麦克风 chunk。
    预期结果：不会写入 user 消息，也不会启动 agent.response.started，只记录
    input_transcript.ignored。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    user_id = "user-echo-guard"
    session_id = "dev-echo-guard"
    stream_id = "stream-echo-guard"
    now_ms = int(time.time() * 1000)
    app.agent_core.open(user_id, session_id)
    app.agent_core._assistant_output_guard_by_user[user_id] = (now_ms - 500, now_ms + 1500)
    app.agent_core.asr_pipeline._providers[stream_id] = ImmediateFinalAsrProvider("我想收割。")

    app.agent_core.append_audio_event(
        StreamChunk(
            user_id=user_id,
            session_id=session_id,
            stream_id=stream_id,
            stream_type="sensor.mic",
            seq=0,
            payload=b"\x01\x00" * 320,
            final=False,
            timestamp_ms=now_ms,
        )
    )

    session_dir = tmp_path / "runs" / user_id / session_id
    agent_events_text = (session_dir / "agent-events.jsonl").read_text(encoding="utf-8")
    messages_path = session_dir / "messages.jsonl"
    messages_text = messages_path.read_text(encoding="utf-8") if messages_path.exists() else ""
    assert "input_transcript.ignored" in agent_events_text
    assert "assistant_output_echo_guard" in agent_events_text
    assert "我想收割" not in messages_text
    assert "agent.response.started" not in agent_events_text


def test_vision_agent_realtime_final_runs_response_in_background(tmp_path) -> None:
    """测试目标：验证实时 ASR final 不会阻塞麦克风 stream worker。

    测试方法：注入立即返回 final 的 ASR provider 和会阻塞的 Text model，并用
    `final=False` 的麦克风 chunk 触发回复。
    预期结果：`append_audio_event()` 在模型放行前返回；随后放行后台线程，助手消息写入。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    user_id = "user-bg-response"
    session_id = "dev-bg-response"
    stream_id = "stream-bg-response"
    model = BlockingVisionModel()
    app.agent_core.vision_model = model
    app.agent_core.open(user_id, session_id)
    app.agent_core.asr_pipeline._providers[stream_id] = ImmediateFinalAsrProvider("给我讲一个超长的故事。")

    started_at = time.time()
    app.agent_core.append_audio_event(
        StreamChunk(
            user_id=user_id,
            session_id=session_id,
            stream_id=stream_id,
            stream_type="sensor.mic",
            seq=0,
            payload=b"\x01\x00" * 320,
            final=False,
        )
    )
    elapsed = time.time() - started_at

    assert elapsed < 0.2
    assert model.started.wait(timeout=1)
    model.release.set()
    deadline = time.time() + 2
    session_dir = tmp_path / "runs" / user_id / session_id
    while time.time() < deadline:
        messages_text = (session_dir / "messages.jsonl").read_text(encoding="utf-8")
        if "后台回复完成" in messages_text:
            break
        time.sleep(0.02)
    else:
        raise AssertionError("background text response did not finish")


def test_vision_agent_old_background_response_is_superseded_by_new_turn(tmp_path) -> None:
    """测试目标：验证旧后台回复被新一轮输入取代后不会继续输出。

    测试方法：首轮实时 ASR final 触发阻塞模型；第二轮实时 ASR final 进入新 generation；
    再放行首轮模型。
    预期结果：只输出第二轮文本，首轮旧回复不会进入输出链路或助手消息。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    user_id = "user-supersede"
    session_id = "dev-supersede"
    first_stream_id = "stream-supersede-1"
    second_stream_id = "stream-supersede-2"
    model = SupersededBlockingVisionModel()
    output_adapter = RecordingOutputAdapter()
    app.agent_core.vision_model = model
    app.agent_core.output_adapter = output_adapter
    app.agent_core.open(user_id, session_id)
    app.agent_core.asr_pipeline._providers[first_stream_id] = ImmediateFinalAsrProvider("给我讲一个超长的故事。")
    app.agent_core.asr_pipeline._providers[second_stream_id] = ImmediateFinalAsrProvider("停一下，换个话题。")

    app.agent_core.append_audio_event(
        StreamChunk(
            user_id=user_id,
            session_id=session_id,
            stream_id=first_stream_id,
            stream_type="sensor.mic",
            seq=0,
            payload=b"\x01\x00" * 320,
            final=False,
        )
    )
    assert model.first_started.wait(timeout=1)

    app.agent_core.append_audio_event(
        StreamChunk(
            user_id=user_id,
            session_id=session_id,
            stream_id=second_stream_id,
            stream_type="sensor.mic",
            seq=1,
            payload=b"\x01\x00" * 320,
            final=False,
        )
    )
    model.first_release.set()

    deadline = time.time() + 2
    session_dir = tmp_path / "runs" / user_id / session_id
    while time.time() < deadline:
        messages_text = (session_dir / "messages.jsonl").read_text(encoding="utf-8")
        if "新回复应该输出" in messages_text:
            break
        time.sleep(0.02)
    else:
        raise AssertionError("new background text response did not finish")

    output_text = "".join(str(item["text"]) for item in output_adapter.calls)
    messages_text = (session_dir / "messages.jsonl").read_text(encoding="utf-8")
    assert "新回复应该输出" in output_text
    assert "旧回复不应该输出" not in output_text
    assert "旧回复不应该输出" not in messages_text


def test_vision_agent_interrupt_immediately_finalizes_partial_message(tmp_path) -> None:
    """测试目标：验证 Text Realtime 打断时立即封存当前助手 partial。

    测试方法：让首轮模型先释放一段文本后阻塞，再模拟 Paraformer sentence_begin
    打断，随后才放行旧模型继续返回。
    预期结果：messages 中先出现带 `<用户打断>` 的 assistant interrupted 记录，
    后续旧模型文本不会追加到 messages，也不会破坏 user/assistant 顺序。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    user_id = "user-interrupt-finalize"
    session_id = "dev-interrupt-finalize"
    stream_id = "stream-interrupt-finalize"
    model = ReleasedThenBlockingVisionModel()
    output_adapter = RecordingOutputAdapter()
    app.agent_core.vision_model = model
    app.agent_core.output_adapter = output_adapter
    app.agent_core.open(user_id, session_id)
    app.agent_core.asr_pipeline._providers[stream_id] = ImmediateFinalAsrProvider("请讲一个很长的故事。")

    app.agent_core.append_audio_event(
        StreamChunk(
            user_id=user_id,
            session_id=session_id,
            stream_id=stream_id,
            stream_type="sensor.mic",
            seq=0,
            payload=b"\x01\x00" * 320,
            final=False,
        )
    )
    assert model.started.wait(timeout=1)

    app.agent_core.on_speech_started(
        user_id,
        session_id,
        stream_id=stream_id,
        reason="paraformer_sentence_begin",
        diagnostics={"sentence_id": 2},
    )
    model.release.set()

    session_dir = tmp_path / "runs" / user_id / session_id
    deadline = time.time() + 2
    while time.time() < deadline:
        messages = [json.loads(line) for line in (session_dir / "messages.jsonl").read_text(encoding="utf-8").splitlines()]
        if any(item.get("event") == "assistant_text.interrupted" for item in messages):
            break
        time.sleep(0.02)
    else:
        raise AssertionError("interrupted assistant message was not finalized")

    time.sleep(0.1)
    messages = [json.loads(line) for line in (session_dir / "messages.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [item["role"] for item in messages[:2]] == ["user", "assistant"]
    assert messages[1]["event"] == "assistant_text.interrupted"
    assert messages[1]["content"] == "这段已经说出口。<用户打断>"
    assert messages[1]["interrupted"] is True
    assert messages[1]["interrupted_reason"] == "paraformer_sentence_begin"
    assert all("这段取消后不应该出现" not in str(item.get("content", "")) for item in messages)
    assert sum(1 for item in messages if item.get("role") == "assistant") == 1


def test_vision_output_estimates_played_prefix_for_interrupt(tmp_path, monkeypatch) -> None:
    """测试目标：验证打断消息可按已播放音频估算文本前缀。

    测试方法：注入固定 10 秒音频的 TTS，并把当前时间固定在首包后 5 秒。
    预期结果：OutputService 返回约一半文本，避免把完整回复误标为已听完。
    """

    class FixedDurationTTS:
        """测试用 TTS：每次合成固定 10 秒 PCM。"""

        def synthesize_delta(self, text: str) -> bytes:
            """返回 10 秒、100Hz、单声道 PCM16 音频。"""

            return b"\x01\x00" * 1000

        def drain_audio(self, wait_seconds: float = 0.0) -> bytes:
            """没有额外后台音频。"""

            return b""

        def finish(self) -> bytes:
            """没有尾包。"""

            return b""

        def metrics(self) -> dict:
            """返回固定首包时间和采样率。"""

            return {"sample_rate_hz": 100, "first_audio_at": 100.0}

    monkeypatch.setattr("realtime_agent.output.service.time.time", lambda: 105.0)
    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    app.output_service.router._injected_tts = FixedDurationTTS()

    app.output_service.on_assistant_vision_delta(
        AssistantTextDelta(user_id="user-prefix", session_id="dev-prefix", text="abcdefghij", final=False)
    )

    assert app.output_service.estimate_played_text_prefix(user_id="user-prefix", session_id="dev-prefix") == "abcde"


def test_vision_interrupt_keeps_generated_unheard_suffix_in_message(tmp_path) -> None:
    """测试目标：验证打断消息保留已经生成但尚未播放的文本。

    测试方法：手动记录一段已释放给 TTS 的 assistant partial，并让输出服务估算用户
    只听到了前两个字。
    预期结果：messages 中 `<用户打断>` 插入在已播放和未播放文本之间，后缀继续作为
    后续模型上下文保留。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    user_id = "user-interrupt-unheard"
    session_id = "dev-interrupt-unheard"
    app.agent_core.open(user_id, session_id)
    generation = app.agent_core._next_response_generation(user_id)
    app.agent_core._remember_assistant_parts(user_id=user_id, generation=generation, parts=["我是乐鑫"])

    def estimate_played_text_prefix(*, user_id: str, session_id: str) -> str:
        """模拟播放进度：用户只听到了前两个字。"""

        return "我是"

    app.agent_core.output_service.estimate_played_text_prefix = estimate_played_text_prefix
    app.agent_core._finalize_interrupted_generation(
        user_id=user_id,
        session_id=session_id,
        generation=generation,
        reason="paraformer_sentence_begin",
    )

    messages = [
        json.loads(line)
        for line in (tmp_path / "runs" / user_id / session_id / "messages.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert messages[-1]["event"] == "assistant_text.interrupted"
    assert messages[-1]["content"] == "我是<用户打断>乐鑫"
    agent_events = (tmp_path / "runs" / user_id / session_id / "agent-events.jsonl").read_text(encoding="utf-8")
    assert '"played_chars": 2' in agent_events
    assert '"unheard_chars": 2' in agent_events


def test_vision_agent_allows_multiple_turns_on_same_mic_stream(tmp_path) -> None:
    """测试目标：验证同一个 sensor.mic stream 内可以连续提交多段用户输入。

    测试方法：用同一个 stream_id 发送两段 final 麦克风输入，只改变 seq 和 mock ASR
    文件名转写。
    预期结果：两段输入都会触发 VisionRealtimeAgentCore 响应，不会因为复用 stream_id 被静默跳过。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    model = CaptureHistoryVisionModel()
    app.agent_core.vision_model = model
    user_id = "user-same-stream"
    session_id = "dev-same-stream"
    stream_id = "stream-in-reused"

    app.agent_core.append_audio_event(
        StreamChunk(
            user_id=user_id,
            session_id=session_id,
            stream_id=stream_id,
            stream_type="sensor.mic",
            seq=10,
            payload=b"first",
            final=True,
            metadata={"source_path": "/tmp/你是谁呀.wav"},
        )
    )
    app.agent_core.append_audio_event(
        StreamChunk(
            user_id=user_id,
            session_id=session_id,
            stream_id=stream_id,
            stream_type="sensor.mic",
            seq=20,
            payload=b"second",
            final=True,
            metadata={"source_path": "/tmp/我刚才问了你什么.wav"},
        )
    )

    session_dir = tmp_path / "runs" / user_id / session_id
    messages_text = (session_dir / "messages.jsonl").read_text(encoding="utf-8")
    assert "你是谁呀" in messages_text
    assert "我刚才问了你什么" in messages_text
    assert len(model.messages) == 2


def test_vision_agent_loads_device_message_history_from_messages_jsonl(tmp_path) -> None:
    """测试目标：验证同一用户同一设备的新一轮文本请求会加载历史消息。

    测试方法：先在 `runs/<user_id>/<device_id>/messages.jsonl` 写入历史 user/assistant
    对话，再触发 VisionRealtimeAgentCore 处理当前 final 麦克风输入。
    预期结果：模型收到的运行时 messages 和 `model-request.json` 都包含历史消息，并以
    当前用户输入收尾。
    """

    app = RealtimeAgentApp(
        RealtimeAgentConfig(
            runs_root=str(tmp_path / "runs"),
            agent_mode="vision",
            vision_max_context_messages=10,
        )
    )
    user_id = "user-browser-glass-001"
    device_id = "dev-browser-glass-001"
    messages_path = tmp_path / "runs" / user_id / device_id / "messages.jsonl"
    messages_path.parent.mkdir(parents=True, exist_ok=True)
    history = [
        {"session_id": device_id, "role": "user", "content": "我刚才说我要去电梯口。"},
        {"session_id": device_id, "role": "assistant", "content": "我会帮你留意去电梯口的路线。"},
    ]
    messages_path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in history) + "\n", encoding="utf-8")
    model = CaptureHistoryVisionModel()
    app.agent_core.vision_model = model

    app.agent_core.append_audio_event(
        StreamChunk(
            user_id=user_id,
            session_id=device_id,
            stream_id="stream-history",
            stream_type="sensor.mic",
            seq=0,
            payload=b"hello",
            final=True,
        )
    )

    runtime_messages = model.messages[0]
    request = json.loads((tmp_path / "runs" / user_id / device_id / "model-request.json").read_text(encoding="utf-8"))
    assert runtime_messages[0]["content"] == "我刚才说我要去电梯口。"
    assert runtime_messages[1]["content"] == "我会帮你留意去电梯口的路线。"
    assert runtime_messages[-1]["role"] == "user"
    assert request["messages"][1]["content"] == "我刚才说我要去电梯口。"
    assert "历史已加载。" in (tmp_path / "runs" / user_id / device_id / "messages.jsonl").read_text(encoding="utf-8")


def test_vision_agent_replays_tool_and_task_results_from_messages_jsonl(tmp_path) -> None:
    """测试目标：验证 Text 新一轮请求会把成对工具/任务结果回灌到 provider messages。

    测试方法：预置 assistant tool_call 和 tool result 历史，再触发一轮Vision 输入。
    预期结果：模型收到合法的 assistant.tool_calls + tool 消息，而不是只收到普通助手文本。
    """

    app = RealtimeAgentApp(
        RealtimeAgentConfig(
            runs_root=str(tmp_path / "runs"),
            agent_mode="vision",
            vision_max_context_messages=10,
        )
    )
    user_id = "user-tool-history"
    device_id = "dev-tool-history"
    messages_path = tmp_path / "runs" / user_id / device_id / "messages.jsonl"
    messages_path.parent.mkdir(parents=True, exist_ok=True)
    history = [
        {"session_id": device_id, "role": "user", "content": "帮我看看红绿灯。"},
        {
            "session_id": device_id,
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-task-1",
                    "name": "start_traffic_light_task",
                    "arguments": {"question": "现在可以过马路了吗"},
                }
            ],
        },
        {
            "session_id": device_id,
            "role": "tool",
            "tool_call_id": "call-task-1",
            "name": "start_traffic_light_task",
            "content": {"ok": True, "tasks": [{"task_id": "task-1", "state": "running"}]},
        },
        {"session_id": device_id, "role": "assistant", "content": "红绿灯任务已启动。"},
    ]
    messages_path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in history) + "\n", encoding="utf-8")
    model = CaptureHistoryVisionModel()
    app.agent_core.vision_model = model

    app.agent_core.append_audio_event(
        StreamChunk(
            user_id=user_id,
            session_id=device_id,
            stream_id="stream-tool-history",
            stream_type="sensor.mic",
            seq=0,
            payload=b"hello",
            final=True,
        )
    )

    runtime_messages = model.messages[0]
    assert [message["role"] for message in runtime_messages[:4]] == ["user", "assistant", "tool", "assistant"]
    assert runtime_messages[1]["tool_calls"][0]["function"]["name"] == "start_traffic_light_task"
    assert json.loads(runtime_messages[1]["tool_calls"][0]["function"]["arguments"]) == {"question": "现在可以过马路了吗"}
    assert runtime_messages[2]["tool_call_id"] == "call-task-1"
    assert json.loads(runtime_messages[2]["content"])["tasks"][0]["state"] == "running"


def test_vision_agent_injects_latest_message_summary_without_duplicate_history(tmp_path) -> None:
    """测试目标：验证Vision 模型请求会注入历史摘要但不重复展开已压缩原始消息。

    测试方法：先通过 ConversationMemoryService 触发一次压缩，再发送新一轮Vision 输入。
    预期结果：system prompt 包含最新摘要；runtime messages 只包含压缩后 active 消息和当前输入。
    """

    app = RealtimeAgentApp(
        RealtimeAgentConfig(
            runs_root=str(tmp_path / "runs"),
            agent_mode="vision",
            vision_max_context_messages=10,
        )
    )
    app.conversation_memory.summarizer = FixedSummaryAgent()
    user_id = "user-summary"
    device_id = "dev-summary"
    for index in range(8):
        app.control_service.append_message(
            user_id,
            {
                "session_id": device_id,
                "role": "user" if index % 2 == 0 else "assistant",
                "content": f"压缩前消息 {index}",
                "created_at": 1_700_000_000 + index,
            },
        )
    app.control_service.compact_messages_if_needed(user_id=user_id, session_id=device_id, threshold=6, keep_latest=2)
    model = CaptureHistoryVisionModel()
    app.agent_core.vision_model = model

    app.agent_core.append_audio_event(
        StreamChunk(
            user_id=user_id,
            session_id=device_id,
            stream_id="stream-summary",
            stream_type="sensor.mic",
            seq=0,
            payload=b"hello",
            final=True,
        )
    )

    request = json.loads((tmp_path / "runs" / user_id / device_id / "model-request.json").read_text(encoding="utf-8"))
    assert "更早历史对话的压缩摘要" in request["messages"][0]["content"]
    assert "压缩前消息 0 已被归档" in request["messages"][0]["content"]
    assert all("压缩前消息 0" not in item.get("content", "") for item in model.messages[0])
    assert model.messages[0][0]["content"] == "压缩前消息 6"
    assert model.messages[0][1]["content"] == "压缩前消息 7"
    assert model.messages[0][-1]["role"] == "user"


class DemoTask(BaseTask):
    """测试用 Task。"""

    task_type = "demo_task"

    async def on_start(self, context) -> None:
        """测试目标：验证 TaskSignal 只能通过 bridge 回流。

        测试方法：启动时提交一个 requires_agent_decision 信号。
        预期结果：TaskSignalBridge 写入 task-signals 和 agent-events。
        """

        context.bridge.handle_signal(
            TaskSignal(
                task_id=context.task_ref.task_id,
                task_type=context.task_ref.task_type,
                signal_name="demo.needs_agent",
                user_id=context.user_id,
                session_id=context.session_id,
                payload={"step": "started"},
                requires_agent_decision=True,
                allow_direct_notify=False,
            )
        )


def test_task_engine_create_query_cancel_and_agent_event_bridge(tmp_path) -> None:
    """测试目标：验证 TaskEngine 支持 create/query/cancel 和 TaskSignalBridge 回流 Agent。

    测试方法：注册 DemoTask 后创建任务，任务启动时发出 requires_agent_decision 事件。
    预期结果：任务进入 started 状态后可查询和取消，runs 中写入 task signal 与 agent context sync 事件。
    """

    import asyncio

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    app.task_engine.register(DemoTask)

    ref = asyncio.run(
        app.task_engine.create(
            task_type="demo_task",
            user_id="user-task",
            session_id="sess-task",
            input_data={"goal": "check"},
        )
    )

    assert app.task_engine.query(ref.task_id).state == "started"
    cancelled = asyncio.run(app.task_engine.cancel(ref.task_id, reason="test_done"))
    assert cancelled.state == "cancelled"

    session_dir = tmp_path / "runs" / "user-task" / "sess-task"
    agent_events = (session_dir / "agent-events.jsonl").read_text(encoding="utf-8")
    task_signals = (session_dir / "task-signals.jsonl").read_text(encoding="utf-8")
    assert "task.requires_agent_context_sync" in agent_events
    assert "demo.needs_agent" in task_signals
