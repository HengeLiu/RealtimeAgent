from __future__ import annotations

from audio_chat import BaseTask, TaskContext, TaskSignal


class TrafficLightTask(BaseTask):
    """红绿灯 mock 视觉识别 Task。

    主要功能：
    1. 请求端侧上传一张 RGB 图片资产。
    2. 在 YOLO 迁移完成前使用 mock 状态生成红绿灯信号。
    3. 通过 Output Service 播报可行动作建议。
    """

    task_type = "traffic_light_task"
    description = "红绿灯连续视觉识别任务"

    async def on_start(self, context: TaskContext) -> None:
        """启动红绿灯识别。

        主要逻辑：请求一张 RGB 图，读取输入中的 mock 状态后生成
        `traffic_light.state_detected` 信号。
        参数：`context` 为 SDK 注入上下文。
        返回值：无。
        异常情况：事件发布或资产读取失败时由 TaskEngine 记录失败。
        """

        if context.devices is None:
            await context.fail("缺少设备上下文")
            return
        input_data = dict(context.metadata.get("input") or {})
        state = str(input_data.get("mock_state") or input_data.get("expected_state") or "green").strip().lower()
        asset = await context.devices.sensors.rgb.one(
            params={
                "format": "jpeg",
                "reason": "traffic_light_mock",
            },
            timeout_seconds=float(input_data.get("timeout_seconds") or 5),
        )

        suggestion = {
            "green": "绿灯，可以在确认安全后通行",
            "red": "红灯，请等待",
            "yellow": "黄灯，请减速等待",
        }.get(state, "未确认红绿灯状态，请谨慎等待")
        if context.bridge is not None:
            context.bridge.handle_signal(
                TaskSignal(
                    task_id=context.task_ref.task_id,
                    task_type=context.task_ref.task_type,
                    signal_name="traffic_light.state_detected",
                    user_id=context.user_id,
                    session_id=context.session_id,
                    payload={
                        "state": state,
                        "suggestion": suggestion,
                        "asset_id": asset.asset_id,
                        "source": "mock",
                    },
                    allow_direct_notify=False,
                )
            )
        await context.output.say(suggestion, priority="high" if state == "green" else "normal")
        await context.complete({"state": state, "asset_id": asset.asset_id}, summary=suggestion)

    async def on_cancel(self, context: TaskContext) -> None:
        """取消红绿灯识别任务。"""

        _ = context
