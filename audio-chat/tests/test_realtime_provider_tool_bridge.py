from __future__ import annotations

import asyncio

from audio_chat.agent_core.realtime import RealtimeToolBridge
from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.protocol import Event, StreamChunk, StreamFormat
from audio_chat.tools import BaseTool, ToolContext, ToolResult


class Connection:
    """测试用端侧连接，收集控制事件和输出音频。"""

    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.events = []
        self.chunks = []

    def push_event(self, event: Event) -> None:
        """记录控制事件。"""

        self.events.append(event)

    def push_stream_chunk(self, chunk: StreamChunk) -> None:
        """记录输出 stream chunk。"""

        self.chunks.append(chunk)


def register_speaker(app: AudioChatApp, connection: Connection, user_id: str) -> None:
    """注册可消费 actuator.speaker 的测试端侧。"""

    app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id=user_id,
            producer_id=connection.device_id,
            payload={
                "device_id": connection.device_id,
                "auth": {"mode": "disabled"},
                "capabilities": {
                    "streams.produce": ["sensor.mic"],
                    "streams.consume": ["actuator.speaker"],
                },
                "subscriptions": [
                    {"event": "stream.output.*", "filter": {"stream_type": "actuator.speaker"}},
                ],
            },
        ),
        connection,
    )


class RealtimeCityTool(BaseTool):
    """测试用 Realtime Tool。

    主要功能：验证 Realtime provider 工具桥只能通过 ToolGateway 调用业务工具。
    主要方法：`run()` 返回参数和用户上下文。
    主要属性：`name` 是 provider function calling schema 中的函数名。
    """

    name = "realtime_city_lookup"
    description = "查询城市信息"

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """执行测试工具。

        功能：返回城市名。
        主要逻辑：让出一次事件循环，模拟真实异步工具。
        参数：`context` 为 SDK 注入上下文；`input_data` 为 provider 聚合参数。
        返回值：成功 ToolResult。
        异常情况：本测试工具不主动抛异常。
        """

        await asyncio.sleep(0)
        return ToolResult.success({"city": input_data["city"], "user_id": context.user_id})


def test_realtime_tool_bridge_commits_json_argument_delta_inside_running_loop(tmp_path) -> None:
    """测试目标：验证 RealtimeToolBridge 在已有事件循环内安全执行工具调用。

    测试方法：先追加两段 JSON arguments delta，再在 `asyncio.run()` 内提交工具调用。
    预期结果：不会嵌套 `asyncio.run()`，ToolResult 正确返回并记录 tool trace。
    """

    async def _run() -> dict:
        app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), agent_mode="realtime_audio", realtime_provider="mock"))
        app.tool_registry.register(RealtimeCityTool())
        bridge = RealtimeToolBridge(tool_gateway=app.tool_gateway, recorder=app.recorder)
        bridge.append_tool_call_delta(
            tool_call_id="rt-call-1",
            name="realtime_city_lookup",
            arguments_delta='{"city": ',
        )
        bridge.append_tool_call_delta(tool_call_id="rt-call-1", arguments_delta='"hangzhou"}')
        return bridge.commit_tool_call(tool_call_id="rt-call-1", user_id="user-rt", session_id="sess-rt")

    result = asyncio.run(_run())

    assert result["ok"] is True
    assert result["data"]["city"] == "hangzhou"
    trace_text = (tmp_path / "runs" / "sessions" / "sess-rt" / "tool-trace.jsonl").read_text(encoding="utf-8")
    assert "realtime_city_lookup" in trace_text


class ToolCallingRealtimeProvider:
    """测试用 provider。

    主要功能：模拟 provider 先发 function call arguments，再输出一片原生音频。
    主要属性：`callbacks` 保存 RealtimeAudioAgentCore 注入的回调集合。
    """

    def __init__(self, config) -> None:
        self.config = config
        self.callbacks = None

    def open(self, *, user_id: str, session_id: str, callbacks) -> None:
        """保存 callbacks 并上报 provider 打开事件。"""

        self.callbacks = callbacks
        callbacks.provider_event({"event": "fake_realtime.session.opened"})

    def append_audio(self, chunk: StreamChunk) -> None:
        """模拟 provider 工具调用和 audio delta。"""

        assert self.callbacks is not None
        assert self.callbacks.tool_call_delta is not None
        assert self.callbacks.tool_call_done is not None
        self.callbacks.tool_call_delta(
            {
                "tool_call_id": "rt-call-2",
                "name": "realtime_city_lookup",
                "arguments_delta": '{"city": "suzhou"}',
            }
        )
        self.callbacks.tool_call_done({"tool_call_id": "rt-call-2"})
        self.callbacks.audio_delta(
            b"\x01\x00" * 320,
            StreamFormat(codec="pcm16le", sample_rate=16000, channels=1, chunk_ms=20),
            {"provider": "fake"},
        )
        self.callbacks.audio_done({"provider": "fake"})

    def commit_input(self, *, user_id: str, session_id: str, reason: str) -> None:
        """当前测试不需要显式提交。"""

    def cancel(self, *, user_id: str, reason: str) -> None:
        """当前测试不需要取消。"""

    def close(self, *, user_id: str, reason: str) -> None:
        """当前测试不需要关闭。"""


def test_realtime_core_records_tool_result_injection_and_audio_output(tmp_path) -> None:
    """测试目标：验证 RealtimeAudioAgentCore 能记录 provider tool call 结果回填。

    测试方法：注入会触发工具调用的 fake provider，并注册 speaker 端侧消费原生音频。
    预期结果：runs 中出现 `realtime.tool_result.ready` 和回填状态，端侧收到音频。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), agent_mode="realtime_audio", realtime_provider="mock"))
    app.tool_registry.register(RealtimeCityTool())
    app.agent_core.provider_factory = lambda config: ToolCallingRealtimeProvider(config)
    connection = Connection("dev-rt")
    register_speaker(app, connection, user_id="user-rt")
    handle = app.open_input_stream(user_id="user-rt", producer_id="dev-rt")

    app.write_input_chunk(
        StreamChunk(
            user_id="user-rt",
            session_id=handle.session_id,
            stream_id=handle.stream_id,
            stream_type="sensor.mic",
            seq=0,
            payload=b"\x00\x00" * 320,
            final=False,
        )
    )

    model_events = (tmp_path / "runs" / "sessions" / handle.session_id / "model-events.jsonl").read_text(encoding="utf-8")
    assert "realtime.tool_result.ready" in model_events
    assert "handled_by_provider_adapter" in model_events
    assert connection.chunks
