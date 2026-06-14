"""VL 链路 late result 注入端到端测试（Phase 4）。

测试目标：验证 VisionRealtimeAgentCore.inject_followup_result 能把 late result 作为
文本驱动 turn 注入活跃会话并产出助手回复，以及 FollowUpRouter 经 VlFollowUpInjector
在会话空闲时驱动 VL 回复。
"""

from __future__ import annotations

import time

import asyncio

from realtime_agent.app import RealtimeAgentApp, RealtimeAgentConfig
from realtime_agent.conversation.follow_up import FollowUpCompletion, FollowUpRouter, VlFollowUpInjector
from realtime_agent.tools import BaseTool, ToolResult, ToolSpec


class _SimpleTextModel:
    """只输出一句固定文本的 mock 视觉模型。"""

    provider_name = "mock-text"
    model = "mock-text-model"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.requests: list[str] = []

    def stream_messages(self, *, messages: list[dict], tools: list[dict]):
        # 记录最后一条用户输入，便于断言 late result 进入了模型请求。
        user_inputs = [m for m in messages if m.get("role") == "user"]
        if user_inputs:
            self.requests.append(str(user_inputs[-1].get("content") or ""))
        yield self.reply

    def cancel(self) -> None:
        """取消 mock 模型。"""


def _vision_app(tmp_path, reply: str):
    """构造 vision 模式 app 并注入固定文本模型，返回 (app, core, model)。"""

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="vision"))
    model = _SimpleTextModel(reply)
    app.agent_core.vision_model = model
    core = getattr(app.agent_core, "core", app.agent_core)
    return app, core, model


def _wait_for(predicate, *, timeout_seconds: float = 3.0) -> bool:
    """轮询等待条件成立。"""

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_vl_inject_followup_result_drives_reply(tmp_path) -> None:
    """测试目标：直接注入 late result 会写入 user 消息并产出助手回复。"""

    app, core, model = _vision_app(tmp_path, reply="路线已经查到，步行大约 800 米。")
    user_id = "user-vl-late"
    session_id = "sess-vl-late"
    core.open(user_id, session_id)

    ok = core.inject_followup_result(
        user_id=user_id,
        session_id=session_id,
        text="（系统通知）刚才发起的路线规划已经有结果了：步行约 800 米。请用一句话告诉用户。",
        run_id="tool_run_x",
    )
    assert ok is True

    session_dir = tmp_path / "runs" / user_id / session_id
    messages_path = session_dir / "messages.jsonl"
    assert _wait_for(lambda: messages_path.exists() and "路线已经查到" in messages_path.read_text(encoding="utf-8"))
    message_text = messages_path.read_text(encoding="utf-8")
    assert "tool_result.late.done" in message_text
    assert "路线已经查到，步行大约 800 米。" in message_text
    # late result 文本确实进入了模型请求。
    assert any("步行约 800 米" in req for req in model.requests)


def test_vl_inject_followup_result_returns_false_when_session_closed(tmp_path) -> None:
    """测试目标：会话未打开时注入返回 False。"""

    app, core, model = _vision_app(tmp_path, reply="不应触发")
    ok = core.inject_followup_result(user_id="u-closed", session_id="s-closed", text="late", run_id="r")
    assert ok is False


def test_router_injects_into_vl_core_when_idle(tmp_path) -> None:
    """测试目标：FollowUpRouter 经 VlFollowUpInjector 在空闲会话驱动 VL 回复。"""

    app, core, model = _vision_app(tmp_path, reply="天气查到了，今天多云转晴。")
    user_id = "user-router-vl"
    session_id = "sess-router-vl"
    core.open(user_id, session_id)

    router = FollowUpRouter(store=app.tool_gateway.tool_run_store, injector=VlFollowUpInjector(core), recorder=app.recorder)
    completion = FollowUpCompletion(
        run_id="tool_run_weather",
        user_id=user_id,
        session_id=session_id,
        tool_name="search_web",
        text="（系统通知）刚才发起的天气查询已经有结果了：今天多云转晴。请用一句话告诉用户。",
    )
    decision = router.submit(completion)
    assert decision == "followed_up"

    session_dir = tmp_path / "runs" / user_id / session_id
    messages_path = session_dir / "messages.jsonl"
    assert _wait_for(lambda: messages_path.exists() and "天气查到了" in messages_path.read_text(encoding="utf-8"))


class _SlowRouteTool(BaseTool):
    """超过窗口的后台路线工具，用于全链路集成测试。"""

    spec = ToolSpec(
        name="slow_route_tool",
        description="慢速路线工具。",
        late_result_policy="background",
        background_timeout_seconds=60,
        follow_up_ttl_seconds=300,
    )

    async def run(self, context, input_data: dict) -> ToolResult:
        import asyncio as _asyncio

        await _asyncio.sleep(0.3)
        return ToolResult.success(data={"route": "ready"}, message="步行约 800 米，预计 12 分钟到达。")


def test_app_background_tool_runs_then_router_drives_vl_reply(tmp_path) -> None:
    """测试目标：全链路——app gateway 后台工具超窗返回 running，完成后经已装配的
    FollowUpRouter 驱动 VL 产出最终回复。

    测试方法：vision app 注册慢 background 工具并打开会话，用极短等待窗口经
    app.tool_gateway.call 调用；后台完成后由 app 内置 router 注入 VL turn。
    预期结果：调用立即返回 running；稍后 messages.jsonl 出现 late 结果与助手回复。
    """

    app, core, model = _vision_app(tmp_path, reply="好的，路线我已经查到：步行约 800 米。")
    # 把等待窗口压短，确保 0.3s 的工具走 background 路径。
    app.tool_gateway.executor.wait_window_seconds = 0.05
    app.tool_registry.register(_SlowRouteTool())
    user_id = "user-fullstack"
    session_id = "sess-fullstack"
    core.open(user_id, session_id)

    result = asyncio.run(
        app.tool_gateway.call(
            name="slow_route_tool",
            user_id=user_id,
            session_id=session_id,
            input_data={"destination": "公园"},
        )
    )
    assert result.status == "running"

    session_dir = tmp_path / "runs" / user_id / session_id
    messages_path = session_dir / "messages.jsonl"
    assert _wait_for(
        lambda: messages_path.exists() and "路线我已经查到" in messages_path.read_text(encoding="utf-8"),
        timeout_seconds=5.0,
    )
    message_text = messages_path.read_text(encoding="utf-8")
    assert "tool_result.late.done" in message_text
    assert "步行约 800 米" in message_text
