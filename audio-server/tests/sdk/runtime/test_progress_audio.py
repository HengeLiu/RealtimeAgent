from __future__ import annotations

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.protocol import Event, StreamChunk
from audio_chat.tools import BaseTool, ToolContext, ToolResult


class Connection:
    """测试用端侧连接，收集输出事件和音频 chunk。"""

    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.events: list[Event] = []
        self.chunks: list[StreamChunk] = []

    def push_event(self, event: Event) -> None:
        """记录控制事件。"""

        self.events.append(event)

    def push_stream_chunk(self, chunk: StreamChunk) -> None:
        """记录输出音频。"""

        self.chunks.append(chunk)


def register_speaker(app: AudioChatApp, connection: Connection, user_id: str = "user-progress") -> None:
    """注册一个可消费 speaker 输出的测试设备。"""

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


class ProgressTool(BaseTool):
    """测试用工具，声明单条和多候选前置播报。"""

    name = "progress_lookup"
    description = "查询进度"
    progress_message = "正在查询"
    progress_messages = ("正在查询", "请稍等")

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """测试目标：验证工具执行结果能回填给模型。

        测试方法：返回固定结果。
        预期结果：模型第二轮可以继续输出文本。
        """

        return ToolResult.success({"ok": True}, message="ready")


class FirstToolCallModel:
    """首输出就是 tool call 的模型。"""

    provider_name = "mock"
    model = "first-tool"

    def __init__(self) -> None:
        self.calls = 0

    def stream_messages(self, *, messages: list[dict], tools: list[dict]):
        """第一轮返回 Tool 调用，第二轮返回最终文本。"""

        self.calls += 1
        if self.calls == 1:
            yield {"type": "tool_call", "id": "call-1", "name": "progress_lookup", "arguments": {}}
            return
        yield "查询完成"

    def stream_text(self, transcript: str):
        """历史接口占位。"""

        yield "unused"

    def cancel(self) -> None:
        """取消测试模型。"""


class TextThenToolCallModel(FirstToolCallModel):
    """先输出文本再输出 tool call 的模型。"""

    model = "text-then-tool"

    def stream_messages(self, *, messages: list[dict], tools: list[dict]):
        """第一轮先输出文本，再发起 Tool 调用。"""

        self.calls += 1
        if self.calls == 1:
            yield "我先想一下。"
            yield {"type": "tool_call", "id": "call-1", "name": "progress_lookup", "arguments": {}}
            return
        yield "查询完成"


def _send_final_mic(app: AudioChatApp, session_id: str) -> None:
    app.agent_core.append_audio_event(
        StreamChunk(
            user_id="user-progress",
            session_id=session_id,
            stream_id="stream-mic",
            stream_type="sensor.mic",
            seq=0,
            payload=b"hello",
            final=True,
        )
    )


def _session_file(tmp_path, session_id: str, name: str):
    """返回当前测试用户会话下的运行产物文件路径。"""

    return tmp_path / "runs" / "user-progress" / session_id / name


def test_progress_audio_only_when_first_model_output_is_tool_call(tmp_path) -> None:
    """测试目标：验证工具前置播报只在模型首输出为 Tool 调用时触发。

    测试方法：用首输出 Tool 调用的模型驱动 TextAgentCore，并注册可消费 speaker 的端侧。
    预期结果：runs 中写入 `tool.progress_message.emitted`，并生成 cached prompt 音频产物。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), agent_mode="text"))
    connection = Connection("dev-speaker")
    register_speaker(app, connection)
    app.tool_registry.register(ProgressTool())
    app.agent_core.text_model = FirstToolCallModel()

    _send_final_mic(app, "sess-progress-tool-first")

    model_events = _session_file(tmp_path, "sess-progress-tool-first", "model-events.jsonl").read_text(encoding="utf-8")
    assert "tool.progress_message.emitted" in model_events
    assert "tool_progress_audio" in model_events
    assert "cached_prompt_audio" in model_events
    assert list((_session_file(tmp_path, "sess-progress-tool-first", "audio").glob("output-*.wav")))


def test_text_gate_preserves_pre_tool_text_and_skips_progress_audio(tmp_path) -> None:
    """测试目标：验证 Text 门控会保留工具前模型提示，并避免重复插入系统前置播报。

    测试方法：模型先输出“我先想一下。”，随后返回 Tool 调用。
    预期结果：工具仍会执行；工具前文本进入最终消息和 TTS；由于模型已经先提示用户，
    系统不再额外播放 `progress_message`。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), agent_mode="text"))
    connection = Connection("dev-speaker")
    register_speaker(app, connection)
    app.tool_registry.register(ProgressTool())
    app.agent_core.text_model = TextThenToolCallModel()

    _send_final_mic(app, "sess-progress-text-first")

    model_events = _session_file(tmp_path, "sess-progress-text-first", "model-events.jsonl").read_text(encoding="utf-8")
    messages = _session_file(tmp_path, "sess-progress-text-first", "messages.jsonl").read_text(encoding="utf-8")
    assert "text.response_gate.released" in model_events
    assert "text.response_gate.discarded" not in model_events
    assert "tool.progress_message.emitted" not in model_events
    assert "我先想一下。" in messages
    assert "查询完成" in messages
    assert "progress_lookup" in _session_file(tmp_path, "sess-progress-text-first", "tool-events.jsonl").read_text(encoding="utf-8")
    assert list((_session_file(tmp_path, "sess-progress-text-first", "audio").glob("output-*.wav")))


def test_progress_audio_realtime_generation_mode(tmp_path) -> None:
    """测试目标：验证工具前置播报支持 realtime TTS 生成模式。

    测试方法：配置 `output_tool_progress_audio_mode=realtime`，驱动首输出 Tool 调用。
    预期结果：runs 中记录 realtime generation_mode，且不会使用 cached prompt source。
    """

    app = AudioChatApp(
        AudioChatConfig(
            runs_root=str(tmp_path / "runs"),
            agent_mode="text",
            output_tool_progress_audio_mode="realtime",
        )
    )
    connection = Connection("dev-speaker")
    register_speaker(app, connection)
    app.tool_registry.register(ProgressTool())
    app.agent_core.text_model = FirstToolCallModel()

    _send_final_mic(app, "sess-progress-realtime")

    model_events = _session_file(tmp_path, "sess-progress-realtime", "model-events.jsonl").read_text(encoding="utf-8")
    assert '"generation_mode": "realtime"' in model_events
    assert "cached_prompt_audio" not in model_events


def test_progress_audio_disabled_mode_does_not_emit_audio(tmp_path) -> None:
    """测试目标：验证可通过配置关闭工具前置播报音频。

    测试方法：配置 `output_tool_progress_audio_mode=disabled` 后直接提交工具前置播报。
    预期结果：返回 None，不向端侧写入音频 chunk，只在 runs 中记录 skipped 诊断。
    """

    app = AudioChatApp(
        AudioChatConfig(
            runs_root=str(tmp_path / "runs"),
            agent_mode="text",
            output_tool_progress_audio_mode="disabled",
        )
    )
    connection = Connection("dev-speaker")
    register_speaker(app, connection)

    decision = app.output_service.submit_tool_progress(
        user_id="user-progress",
        session_id="sess-progress-disabled",
        tool_name="capture_photo",
        messages=["我先拍张照片看看。"],
    )

    model_events = (tmp_path / "runs" / "_unbound" / "sess-progress-disabled" / "model-events.jsonl").read_text(encoding="utf-8")
    assert decision is None
    assert not connection.chunks
    assert "tool.progress_audio.skipped" in model_events
