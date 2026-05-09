import pytest

from audio_chat.agent_core.base import AgentCoreEvent
from audio_chat.agent_core.router import AgentCoreRouter
from audio_chat.agent_core.realtime import RealtimeAudioAgentCore
from audio_chat.agent_core.text import TextAgentCore
from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.protocol import StreamChunk
from audio_chat.tasks import BaseTask, TaskSignal
from audio_chat.tools import BaseTool, ToolContext, ToolResult


def test_agent_mode_text_builds_text_core(tmp_path) -> None:
    """测试目标：验证 `agent.mode=text` 是当前可运行 Agent Core。

    测试方法：用 text 模式创建 AudioChatApp。
    预期结果：app 正常初始化，并带有 `append_audio_event` 方法。
    """
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), agent_mode="text"))

    assert isinstance(app.agent_core, TextAgentCore)
    assert hasattr(app.agent_core, "append_audio_event")


def test_agent_mode_realtime_audio_builds_realtime_core(tmp_path) -> None:
    """测试目标：验证 `agent.mode=realtime_audio` 能创建 RealtimeAudioAgentCore。

    测试方法：用 `agent_mode=realtime_audio` 创建 AudioChatApp。
    预期结果：app 正常初始化，不在构造阶段连接真实 provider。
    """
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), agent_mode="realtime_audio"))

    assert isinstance(app.agent_core, RealtimeAudioAgentCore)


def test_agent_mode_auto_defaults_to_text_for_now(tmp_path) -> None:
    """测试目标：验证 `agent.mode=auto` 当前保守落到文本链路。

    测试方法：用 auto 模式创建 AudioChatApp。
    预期结果：返回 TextAgentCore；文档中声明后续再接端侧能力判断。
    """
    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), agent_mode="auto"))

    assert isinstance(app.agent_core, TextAgentCore)


def test_agent_mode_custom_fails_fast(tmp_path) -> None:
    """测试目标：验证 custom 模式没有 app-module 工厂时明确失败。

    测试方法：用 custom 模式创建 AudioChatApp。
    预期结果：抛出 NotImplementedError。
    """
    with pytest.raises(NotImplementedError, match="custom"):
        AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), agent_mode="custom"))


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


def test_text_agent_core_exposes_unified_lifecycle_events(tmp_path) -> None:
    """测试目标：验证 TextAgentCore 实现统一 AgentCore 生命周期接口。

    测试方法：创建 text app 后调用 open、commit_input、interrupt、close。
    预期结果：`events()` 返回对应统一事件名。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), agent_mode="text"))

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
        预期结果：TextAgentCore 可通过 ToolGateway 取得该结果并回填模型消息。
        """

        return ToolResult.success(
            data={"city": input_data["city"], "user_id": context.user_id},
            message="weather ready",
        )


class ToolCallingTextModel:
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
        tool_message = next(item for item in messages if item["role"] == "tool")
        assert tool_message["content"]["data"]["city"] == "shanghai"
        yield "上海天气已查询。"

    def stream_text(self, transcript: str):
        yield "unused"

    def cancel(self) -> None:
        pass


def test_text_agent_core_calls_tool_gateway_and_continues_model_loop(tmp_path) -> None:
    """测试目标：验证 TextAgentCore 通过 ToolGateway 调用 Tool 并继续模型循环。

    测试方法：注入会产生一次 tool_call 的 mock 文本模型和测试 Tool，发送 final mic chunk。
    预期结果：工具结果写入消息历史，模型第二轮生成最终回复，tool trace 被记录。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), agent_mode="text"))
    app.tool_registry.register(WeatherTool())
    app.agent_core.text_model = ToolCallingTextModel()
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

    assert "tool.result" in message_text
    assert "上海天气已查询。" in message_text
    assert "lookup_weather" in trace_text
    assert "You are the audio-chat TextAgentCore." in model_request
    assert "lookup_weather" in model_request


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
    预期结果：任务可查询和取消，runs 中写入 task signal 与 agent context sync 事件。
    """

    import asyncio

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    app.task_engine.register(DemoTask)

    ref = asyncio.run(
        app.task_engine.create(
            task_type="demo_task",
            user_id="user-task",
            session_id="sess-task",
            input_data={"goal": "check"},
        )
    )

    assert app.task_engine.query(ref.task_id).state == "running"
    cancelled = asyncio.run(app.task_engine.cancel(ref.task_id, reason="test_done"))
    assert cancelled.state == "cancelled"

    session_dir = tmp_path / "runs" / "user-task" / "sess-task"
    agent_events = (session_dir / "agent-events.jsonl").read_text(encoding="utf-8")
    task_signals = (session_dir / "task-signals.jsonl").read_text(encoding="utf-8")
    assert "task.requires_agent_context_sync" in agent_events
    assert "demo.needs_agent" in task_signals
