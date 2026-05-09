from __future__ import annotations

from audio_chat import BaseTask, TaskContext, TaskEvent


class TrafficLightTask(BaseTask):
    """红绿灯视觉识别 Task。

    主要功能：
    1. 请求端侧上传连续 RGB 帧。
    2. 使用 mock 视觉状态生成红绿灯事件。
    3. 通过 Output Service 播报可行动作建议。
    """

    task_type = "traffic_light_task"
    description = "红绿灯连续视觉识别任务"

    async def on_start(self, context: TaskContext) -> None:
        """启动红绿灯识别。

        主要逻辑：读取若干 RGB 帧后生成 `traffic_light.state_detected` 事件。
        参数：`context` 为 SDK 注入上下文。
        返回值：无。
        异常情况：事件发布或资产读取失败时由 TaskEngine 记录失败。
        """

        if context.devices is None:
            return
        input_data = dict(context.metadata.get("input") or {})
        state = str(input_data.get("expected_state") or "green").strip().lower()
        frame_limit = int(input_data.get("frame_limit") or 2)
        correlation_id = str(input_data.get("correlation_id") or context.task_ref.task_id)
        assets = []
        async for asset in context.devices.sensors.rgb.stream(
            fps=2,
            duration_seconds=1,
            sample_count=frame_limit,
            params={
                "format": "jpeg",
                "asset_policy": "cache",
                "correlation_id": correlation_id,
                "frame_limit": frame_limit,
                "vision_task": "traffic_light",
            },
            timeout_seconds=float(input_data.get("timeout_seconds") or 2),
        ):
            assets.append(asset)
            if len(assets) >= frame_limit:
                break

        suggestion = {
            "green": "绿灯，可以在确认安全后通行",
            "red": "红灯，请等待",
            "yellow": "黄灯，请减速等待",
        }.get(state, "未确认红绿灯状态，请谨慎等待")
        if context.bridge is not None:
            context.bridge.handle_event(
                TaskEvent(
                    task_id=context.task_ref.task_id,
                    task_type=context.task_ref.task_type,
                    event_name="traffic_light.state_detected",
                    user_id=context.user_id,
                    session_id=context.session_id,
                    payload={
                        "state": state,
                        "suggestion": suggestion,
                        "frame_count": len(assets),
                        "asset_ids": [asset.asset_id for asset in assets],
                    },
                    allow_direct_notify=False,
                )
            )
        await context.output.say(suggestion, priority="high" if state == "green" else "normal")
        await context.complete({"state": state, "frame_count": len(assets)}, summary=suggestion)

    async def on_cancel(self, context: TaskContext) -> None:
        """取消红绿灯识别任务。"""

        _ = context
