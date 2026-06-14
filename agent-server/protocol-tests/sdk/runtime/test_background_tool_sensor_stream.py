"""background 工具消费连续 sensor.rgb 流的契约测试。

测试目标：验证声明 `late_result_policy="background"` 的工具可以在后台事件循环上通过
`context.devices.sensors.rgb.stream(...)` 异步消费端侧持续 sensor 帧，资产正常落地，
通讯不引入 device_id 点对点 RPC（mode=continuous）；取消后台运行时通过事件请求端侧
停止采集（stream.control.close.requested, mode=stop）。

这是被删除的 acceptance/test_task_device_stream_contract.py 的 background-tool 版本：
原文件基于已删除的 BaseTask/TaskContext/TaskEngine，Task 概念并入 Tool 后，被验证的能力
本身（BackgroundDeviceFacade 的 allow_stream sensor stream）保留在 tools.py 中，只是改由
background 工具承载。

cross-loop 说明：`stream()` 的异步生成器跑在 ToolRunRunner 的后台事件循环上，而端侧帧由
测试主线程通过 `app.write_input_chunk` 上传。二者跨事件循环仍能联通，因为
`AssetService.watch_assets` 轮询的是受锁保护的共享 `AssetStore`，而非按 loop 绑定的队列
（命令回报路径才依赖 CommandResultBroker 的 (loop,queue) 跨 loop 投递）。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from realtime_agent import RealtimeAgentApp, RealtimeAgentConfig
from realtime_agent.protocol import Event, StreamChunk, StreamFormat
from realtime_agent.tools import BaseTool, ToolContext, ToolResult, ToolSpec


pytestmark = pytest.mark.sdk


class RgbStreamEndpoint:
    """测试用 RGB 端侧。

    主要功能：记录服务端下发的 stream 控制事件；帧上传由测试主线程显式触发，以复现
    “后台事件循环消费 / 主线程推送”的 cross-loop 场景。
    """

    def __init__(self, *, app: RealtimeAgentApp, user_id: str, device_id: str) -> None:
        self.app = app
        self.user_id = user_id
        self.device_id = device_id
        self.events: list[Event] = []

    def push_event(self, event: Event) -> None:
        """记录服务端请求的 stream 配置事件。"""

        self.events.append(event)

    def push_stream_chunk(self, chunk: StreamChunk) -> None:
        """当前测试不消费 output stream。"""

    def close(self, *, reason: str) -> None:
        """关闭测试连接。"""

    def wait_for_open(self, *, stream_type: str = "sensor.rgb", timeout: float = 2.0) -> Event:
        """等待并返回端侧收到的连续采集开启事件。"""

        deadline = time.time() + timeout
        while time.time() < deadline:
            for event in self.events:
                if (
                    event.event_name == "stream.control.open.requested"
                    and event.stream_type == stream_type
                    and (event.payload or {}).get("mode") == "continuous"
                ):
                    return event
            time.sleep(0.02)
        raise AssertionError("未收到端侧 stream.control.open.requested(continuous)")

    def upload_frames(self, *, count: int = 3, correlation_id: str | None = None) -> None:
        """从主线程上传若干 sensor.rgb 帧。"""

        handle = self.app.open_input_stream(
            user_id=self.user_id,
            producer_id=self.device_id,
            stream_type="sensor.rgb",
            format=StreamFormat(codec="jpeg", sample_rate=1, channels=1, chunk_ms=1),
        )
        for seq in range(count):
            self.app.write_input_chunk(
                StreamChunk(
                    user_id=self.user_id,
                    session_id=handle.session_id,
                    stream_id=handle.stream_id,
                    stream_type="sensor.rgb",
                    seq=seq,
                    payload=b"\xff\xd8stream-frame-%d\xff\xd9" % seq,
                    codec="jpeg",
                    sample_rate=1,
                    channels=1,
                    duration_ms=1,
                    final=seq == count - 1,
                    metadata={"correlation_id": correlation_id},
                )
            )
        self.app.stream_service.close_stream(handle.stream_id, reason="fixture_done")

    def stop_events(self, *, stream_type: str = "sensor.rgb") -> list[Event]:
        """返回端侧收到的停止采集事件。"""

        return [
            event
            for event in self.events
            if event.stream_type == stream_type and (event.payload or {}).get("mode") == "stop"
        ]


class ContinuousRgbStreamTool(BaseTool):
    """收集若干连续 RGB 帧后返回的 background 工具。"""

    target_frames = 2

    spec = ToolSpec(
        name="continuous_rgb_stream_tool",
        description="在端侧持续采集 RGB 帧并收集若干帧（演示 background 工具消费连续 sensor 流）。",
        late_result_policy="background",
        background_timeout_seconds=60,
        cancel_supported=True,
        running_message="好的，我开始持续看了。",
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        refs = []
        async for ref in context.devices.sensors.rgb.stream(fps=2, timeout_seconds=5):
            refs.append(ref)
            if len(refs) >= self.target_frames:
                break
        return ToolResult.success(
            data={"frame_count": len(refs), "asset_ids": [ref.asset_id for ref in refs]},
            message=f"收集到 {len(refs)} 帧。",
        )


class EndlessRgbStreamTool(BaseTool):
    """持续消费 RGB 帧、不主动完成的 background 工具（用于取消测试）。"""

    spec = ToolSpec(
        name="endless_rgb_stream_tool",
        description="持续采集 RGB 帧直到被取消。",
        late_result_policy="background",
        background_timeout_seconds=60,
        cancel_supported=True,
        running_message="好的，我一直看着。",
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        count = 0
        async for _ref in context.devices.sensors.rgb.stream(fps=1, timeout_seconds=30):
            count += 1
        return ToolResult.success(data={"frame_count": count})


def register_endpoint(app: RealtimeAgentApp, endpoint: RgbStreamEndpoint) -> None:
    """注册 RGB 生产端点。"""

    response = app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id=endpoint.user_id,
            producer_id=endpoint.device_id,
            payload={
                "device_id": endpoint.device_id,
                "supports": {"sensors": [{"type": "rgb"}], "actuators": []},
                "auth": {"mode": "disabled"},
            },
        ),
        endpoint,
    )
    assert response.event_name == "control.device.registered"


def _wait_terminal(app: RealtimeAgentApp, run_id: str, *, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline and not app.tool_gateway.tool_run_store.get(run_id).is_terminal:
        time.sleep(0.02)


def test_background_tool_streams_continuous_sensor_via_event_and_asset(tmp_path) -> None:
    """测试目标：background 工具通过 event + asset 消费连续 sensor.rgb，资产落地且不引入 device_id RPC。

    测试方法：注册端点和工具，把等待窗口调短让工具走后台；主线程在收到连续采集开启事件后
    跨 loop 上传帧。
    预期结果：工具收集到 >=2 帧后完成（completed_late），资产落地，开启事件 payload 为
    mode=continuous 且不含 device_id。
    """

    app = RealtimeAgentApp(
        RealtimeAgentConfig(
            runs_root=str(tmp_path / "runs"),
            asset_root=str(tmp_path / "assets"),
            agent_mode="vision",
        )
    )
    app.tool_gateway.executor.wait_window_seconds = 0.1
    app.tool_registry.register(ContinuousRgbStreamTool())
    user_id = "user-rgb-stream"
    endpoint = RgbStreamEndpoint(app=app, user_id=user_id, device_id="dev-rgb")
    register_endpoint(app, endpoint)

    result = asyncio.run(
        app.tool_gateway.call(
            name="continuous_rgb_stream_tool",
            user_id=user_id,
            session_id="sess-rgb",
            input_data={},
        )
    )
    assert result.status == "running"
    run_id = result.data["tool_run_id"]

    open_event = endpoint.wait_for_open()
    # 仅靠 event + stream 通讯：开启事件是连续采集，且不携带 device_id 点对点寻址。
    assert open_event.payload["mode"] == "continuous"
    assert "device_id" not in open_event.payload

    endpoint.upload_frames(count=3, correlation_id=open_event.payload.get("correlation_id"))

    _wait_terminal(app, run_id)
    run = app.tool_gateway.tool_run_store.get(run_id)
    assert run.state in {"completed_late", "followed_up"}
    assert run.result["data"]["frame_count"] >= 2
    assert len(app.asset_service.query_assets(user_id=user_id, stream_type="sensor.rgb")) >= 2


def test_cancelling_background_stream_tool_publishes_stop_event(tmp_path) -> None:
    """测试目标：取消持续消费 sensor.rgb 的 background 工具时，通过事件请求端侧停止采集。

    测试方法：启动一个不自动完成的 stream 工具走后台，确认端侧已开始采集后取消该运行。
    预期结果：运行进入 cancelled，端侧收到 mode=stop 的停止采集事件（cross-loop 投递）。
    """

    app = RealtimeAgentApp(
        RealtimeAgentConfig(
            runs_root=str(tmp_path / "runs"),
            asset_root=str(tmp_path / "assets"),
            agent_mode="vision",
        )
    )
    app.tool_gateway.executor.wait_window_seconds = 0.1
    app.tool_registry.register(EndlessRgbStreamTool())
    user_id = "user-rgb-cancel"
    endpoint = RgbStreamEndpoint(app=app, user_id=user_id, device_id="dev-rgb-cancel")
    register_endpoint(app, endpoint)

    result = asyncio.run(
        app.tool_gateway.call(
            name="endless_rgb_stream_tool",
            user_id=user_id,
            session_id="sess-rgb-cancel",
            input_data={},
        )
    )
    assert result.status == "running"
    run_id = result.data["tool_run_id"]

    open_event = endpoint.wait_for_open()
    assert open_event.payload["mode"] == "continuous"
    assert "device_id" not in open_event.payload

    cancel_result = app.tool_gateway.executor.cancel_run(run_id, reason="unit_cancel")
    assert cancel_result["ok"] is True

    _wait_terminal(app, run_id)
    assert app.tool_gateway.tool_run_store.get(run_id).state == "cancelled"

    deadline = time.time() + 2.0
    while time.time() < deadline and not endpoint.stop_events():
        time.sleep(0.02)
    stop_events = endpoint.stop_events()
    assert stop_events, "取消后端侧未收到 mode=stop 的停止采集事件"
    assert stop_events[-1].event_name == "stream.control.close.requested"
