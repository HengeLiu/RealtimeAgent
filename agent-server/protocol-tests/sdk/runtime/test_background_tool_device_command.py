"""background 工具内联消费端侧长命令测试（Phase D）。

测试目标：验证 background 工具的协程可以发起端侧长命令、用 CommandHandle.results()
内联消费 accepted/progress/completed 回报并据此返回结果；设备离线时挂起的 results()
被失败唤醒；fail_fast 工具不能发起长命令。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from realtime_agent import RealtimeAgentApp, RealtimeAgentConfig
from realtime_agent.protocol import Event
from realtime_agent.tools import BaseTool, ToolContext, ToolResult, ToolSpec


pytestmark = pytest.mark.sdk


class RecordingEndpoint:
    """记录端侧收到的控制事件。"""

    def __init__(self, *, user_id: str, device_id: str) -> None:
        self.user_id = user_id
        self.device_id = device_id
        self.events: list[Event] = []
        self.chunks: list = []
        self.closed_reasons: list[str] = []

    def push_event(self, event: Event) -> None:
        self.events.append(event)

    def push_stream_chunk(self, chunk: object) -> None:
        self.chunks.append(chunk)

    def close(self, *, reason: str) -> None:
        self.closed_reasons.append(reason)


def _register_phone(app: RealtimeAgentApp, endpoint: RecordingEndpoint) -> None:
    response = app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id=endpoint.user_id,
            producer_id=endpoint.device_id,
            payload={
                "device_id": endpoint.device_id,
                "device_name": endpoint.device_id,
                "client_type": "background-tool-test",
                "sdk_version": "realtime-agent-test",
                "auth": {"mode": "disabled"},
                "supports": {"sensors": [], "actuators": []},
                "properties": {"device_role": "phone"},
                "support_routes": [{"event": "command.*"}],
            },
        ),
        endpoint,
    )
    assert response.event_name == "control.device.registered"


class FindObjectDemoTool(BaseTool):
    """find_object 形态的内联长命令样板工具。"""

    spec = ToolSpec(
        name="find_object_demo",
        description="在端侧持续寻找物品（演示内联长命令消费）。",
        late_result_policy="background",
        background_timeout_seconds=60,
        cancel_supported=True,
        running_message="好的，我开始找了，找到会告诉你。",
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        handle = await context.devices.commands.start(
            name="peer.video.receiver",
            selector={"device_role": "phone"},
            params={"target": input_data.get("target")},
        )
        try:
            async for event in handle.results():
                if event.state == "completed":
                    # 自然完成：端侧命令已终态，无需再 stop。
                    return ToolResult.success(data=dict(event.data), message=str(event.data.get("message") or "找到了。"))
                if event.state == "failed":
                    return ToolResult.success(data=dict(event.data), message="这次没有找到，端侧没能完成。")
            return ToolResult.success(message="结束了。")
        except asyncio.CancelledError:
            # 被取消时尽力通知端侧停止采集，再向上传播取消。
            await handle.stop(reason="tool_run_cancelled")
            raise


def _command_id(endpoint: RecordingEndpoint) -> str:
    deadline = time.time() + 2.0
    while time.time() < deadline:
        for event in reversed(endpoint.events):
            cid = (event.payload or {}).get("command_id")
            if cid and (event.payload or {}).get("mode") == "start":
                return str(cid)
        time.sleep(0.02)
    raise AssertionError("未收到端侧 command.requested(start)")


def test_background_tool_consumes_command_results_inline(tmp_path) -> None:
    """测试目标：background 工具内联消费 progress/completed 回报并据此返回结果。"""

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    app.tool_gateway.executor.wait_window_seconds = 0.1
    app.tool_registry.register(FindObjectDemoTool())
    user_id = "user-find"
    endpoint = RecordingEndpoint(user_id=user_id, device_id="dev-phone")
    _register_phone(app, endpoint)

    result = asyncio.run(
        app.tool_gateway.call(name="find_object_demo", user_id=user_id, session_id="sess-find", input_data={"target": "手机"})
    )
    assert result.status == "running"
    run_id = result.data["tool_run_id"]

    command_id = _command_id(endpoint)
    app.publish_control_event(
        Event(event_name="command.progress", user_id=user_id, producer_id="dev-phone", payload={"command_id": command_id, "status": "scanning"})
    )
    app.publish_control_event(
        Event(
            event_name="command.completed",
            user_id=user_id,
            producer_id="dev-phone",
            payload={"command_id": command_id, "message": "找到了，在沙发上。"},
        )
    )

    deadline = time.time() + 3.0
    while time.time() < deadline and not app.tool_gateway.tool_run_store.get(run_id).is_terminal:
        time.sleep(0.02)
    run = app.tool_gateway.tool_run_store.get(run_id)
    assert run.state in {"completed_late", "followed_up"}
    assert "找到了" in str(run.result.get("message") or "")


def test_background_tool_command_failure_returns_result(tmp_path) -> None:
    """测试目标：端侧 command.failed 时内联消费得到失败回报并返回安全文案。"""

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    app.tool_gateway.executor.wait_window_seconds = 0.1
    app.tool_registry.register(FindObjectDemoTool())
    user_id = "user-find-fail"
    endpoint = RecordingEndpoint(user_id=user_id, device_id="dev-phone")
    _register_phone(app, endpoint)

    result = asyncio.run(
        app.tool_gateway.call(name="find_object_demo", user_id=user_id, session_id="sess-find-fail", input_data={"target": "钥匙"})
    )
    run_id = result.data["tool_run_id"]
    command_id = _command_id(endpoint)
    app.publish_control_event(
        Event(event_name="command.failed", user_id=user_id, producer_id="dev-phone", payload={"command_id": command_id, "message": "camera busy"})
    )

    deadline = time.time() + 3.0
    while time.time() < deadline and not app.tool_gateway.tool_run_store.get(run_id).is_terminal:
        time.sleep(0.02)
    run = app.tool_gateway.tool_run_store.get(run_id)
    assert run.state in {"completed_late", "followed_up"}
    assert "没有找到" in str(run.result.get("message") or "")


def test_fail_fast_tool_cannot_start_long_command(tmp_path) -> None:
    """测试目标：fail_fast 工具的上下文不允许发起长命令。"""

    class _ShortTool(BaseTool):
        spec = ToolSpec(name="short_cmd_tool", description="前台短工具。")

        async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
            await context.devices.commands.start(name="peer.video.receiver", selector={"device_role": "phone"})
            return ToolResult.success()

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    app.tool_registry.register(_ShortTool())
    endpoint = RecordingEndpoint(user_id="user-short", device_id="dev-phone")
    _register_phone(app, endpoint)

    result = asyncio.run(
        app.tool_gateway.call(name="short_cmd_tool", user_id="user-short", session_id="sess-short", input_data={})
    )
    assert result.ok is False
    assert result.error is not None
