from __future__ import annotations

import asyncio

from realtime_agent.app import RealtimeAgentApp, RealtimeAgentConfig
from realtime_agent.protocol import Event, StreamChunk, StreamFormat
from realtime_agent.tasks import BaseTask, TaskContext, TaskSignal


class RgbTaskEndpoint:
    """测试用 RGB 端侧。

    主要功能：只响应 stream.control.open.requested，通过 sensor.rgb stream 上传帧。
    """

    def __init__(self, *, app: RealtimeAgentApp, user_id: str, device_id: str) -> None:
        self.app = app
        self.user_id = user_id
        self.device_id = device_id
        self.events: list[Event] = []

    def push_event(self, event: Event) -> None:
        """处理服务端请求的 stream 配置事件。"""

        self.events.append(event)
        if event.event_name != "stream.control.open.requested" or event.stream_type != "sensor.rgb":
            return
        if event.payload.get("mode") != "continuous":
            return
        handle = self.app.open_input_stream(
            user_id=self.user_id,
            producer_id=self.device_id,
            stream_type="sensor.rgb",
            format=StreamFormat(codec="jpeg", sample_rate=1, channels=1, chunk_ms=1),
        )
        for seq in range(3):
            self.app.write_input_chunk(
                StreamChunk(
                    user_id=self.user_id,
                    session_id=handle.session_id,
                    stream_id=handle.stream_id,
                    stream_type="sensor.rgb",
                    seq=seq,
                    payload=b"\xff\xd8task-frame-%d\xff\xd9" % seq,
                    codec="jpeg",
                    sample_rate=1,
                    channels=1,
                    duration_ms=1,
                    final=seq == 2,
                    metadata={"correlation_id": event.payload.get("correlation_id")},
                )
            )
        self.app.stream_service.close_stream(handle.stream_id, reason="fixture_done")

    def push_stream_chunk(self, chunk: StreamChunk) -> None:
        """当前测试不消费 output stream。"""

    def close(self, *, reason: str) -> None:
        """关闭测试连接。"""


class ContinuousRgbProductionTask(BaseTask):
    """测试用持续 RGB 任务。"""

    task_type = "continuous_rgb_production"
    timeout_seconds = 2
    cancel_supported = True

    async def on_start(self, context: TaskContext) -> None:
        """测试目标：验证 Task 只通过 event + stream 消费持续 RGB。

        测试方法：发布 configure event，随后通过 watch_assets 读取关联帧。
        预期结果：收集到两帧后进入 finished。
        """

        refs = []
        async for ref in context.devices.sensors.rgb.stream(
            fps=2,
            timeout_seconds=1,
        ):
            refs.append(ref)
            if len(refs) >= 2:
                break
        context.bridge.handle_signal(
            TaskSignal(
                task_id=context.task_ref.task_id,
                task_type=context.task_ref.task_type,
                signal_name="continuous_rgb_production.frames_collected",
                user_id=context.user_id,
                session_id=context.session_id,
                payload={"frame_count": len(refs), "asset_ids": [ref.asset_id for ref in refs]},
                allow_direct_notify=False,
            )
        )
        await context.complete({"frame_count": len(refs)})

    async def on_cancel(self, context: TaskContext) -> None:
        """取消时发布停止采集事件。"""

        return None


class CancellableRgbTask(ContinuousRgbProductionTask):
    """测试用可取消 RGB 任务。"""

    task_type = "cancellable_rgb_production"
    start_result_timeout_seconds = 0.05

    async def on_start(self, context: TaskContext) -> None:
        """只请求端侧开始上传，不主动完成。"""

        async for _ref in context.devices.sensors.rgb.stream(
            fps=1,
            timeout_seconds=10,
        ):
            await asyncio.sleep(0.01)


def register_endpoint(app: RealtimeAgentApp, endpoint: RgbTaskEndpoint) -> None:
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


def test_continuous_sensor_task_uses_only_event_and_stream(tmp_path) -> None:
    """测试目标：验收 Task Engine 生产化的连续 sensor.rgb 场景。

    测试方法：注册端点和 Task，Task 发布 configure event，端点通过 stream 上传帧。
    预期结果：任务完成、资产落地、task signal 记录帧数，未引入 device_id RPC。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), asset_root=str(tmp_path / "assets")))
    app.task_engine.register(ContinuousRgbProductionTask)
    endpoint = RgbTaskEndpoint(app=app, user_id="user-rgb", device_id="dev-rgb")
    register_endpoint(app, endpoint)
    session_id = app.active_session_id("user-rgb")

    ref = asyncio.run(
        app.task_engine.create(
            task_type="continuous_rgb_production",
            user_id="user-rgb",
            session_id=session_id,
        )
    )

    assert ref.state == "finished"
    rgb_events = [event for event in endpoint.events if event.stream_type == "sensor.rgb"]
    assert rgb_events[0].event_name == "stream.control.open.requested"
    assert endpoint.events[0].payload["mode"] == "continuous"
    assert "device_id" not in endpoint.events[0].payload
    assert len(app.asset_service.query_assets(user_id="user-rgb", stream_type="sensor.rgb")) >= 2

    task_signals = (app.recorder.session_dir(session_id, user_id="user-rgb") / "task-signals.jsonl").read_text(
        encoding="utf-8"
    )
    assert "continuous_rgb_production.frames_collected" in task_signals
    assert "task.finished" in task_signals


def test_cancelling_sensor_task_publishes_stop_configure_event(tmp_path) -> None:
    """测试目标：验证取消持续 sensor 任务时通过 event 请求端侧停止。

    测试方法：创建不自动完成的任务后调用 cancel。
    预期结果：端侧收到第二个 configure event，payload.mode 为 stop。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), asset_root=str(tmp_path / "assets")))
    app.task_engine.register(CancellableRgbTask)
    endpoint = RgbTaskEndpoint(app=app, user_id="user-rgb-cancel", device_id="dev-rgb-cancel")
    register_endpoint(app, endpoint)
    session_id = app.active_session_id("user-rgb-cancel")

    ref = asyncio.run(
        app.task_engine.create(
            task_type="cancellable_rgb_production",
            user_id="user-rgb-cancel",
            session_id=session_id,
        )
    )
    cancelled = asyncio.run(app.task_engine.cancel(ref.task_id, reason="unit_cancel"))

    assert cancelled.state == "cancelled"
    assert [event.payload["mode"] for event in endpoint.events if event.stream_type == "sensor.rgb"][-1] == "stop"
