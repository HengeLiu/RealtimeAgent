from __future__ import annotations

from audio_chat import BaseTask, TaskContext, TaskSignal


class FindObjectTask(BaseTask):
    """找物 mock 视觉 Task。

    主要功能：
    1. 通过 typed sensor API 请求一张 RGB 图片资产。
    2. 在 YOLO 迁移完成前使用可解释 mock 逻辑生成找物结果。
    3. 用 TaskSignal 回流结构化结果，并通过 Output Service 通知用户。
    """

    task_type = "find_object_task"
    description = "找物 mock 视觉任务"

    async def on_start(self, context: TaskContext) -> None:
        """启动找物视觉任务。

        主要逻辑：按任务输入读取目标，端侧图片仍通过 `sensor.rgb` stream 上传为资产；
        当前只做 mock 判定，不引入 YOLO 或端侧视觉算法依赖。
        参数：`context` 为 SDK 注入的任务上下文。
        返回值：无。
        异常情况：事件发布或资产读取失败时由 TaskEngine 记录失败。
        """

        if context.devices is None:
            await context.fail("缺少设备上下文")
            return
        input_data = dict(context.metadata.get("input") or {})
        object_name = str(input_data.get("object_name") or input_data.get("target") or "目标物").strip()
        asset = await context.devices.sensors.rgb.one(
            params={
                "format": "jpeg",
                "reason": "find_object_mock",
                "object_name": object_name,
            },
            timeout_seconds=float(input_data.get("timeout_seconds") or 5),
        )

        mock_found = bool(input_data.get("mock_found", True))
        confidence = float(input_data.get("mock_confidence", 0.72 if mock_found else 0.18))
        if context.bridge is not None:
            context.bridge.handle_signal(
                TaskSignal(
                    task_id=context.task_ref.task_id,
                    task_type=context.task_ref.task_type,
                    signal_name="find_object.found" if mock_found else "find_object.not_found",
                    user_id=context.user_id,
                    session_id=context.session_id,
                    payload={
                        "object_name": object_name,
                        "found": mock_found,
                        "confidence": confidence,
                        "asset_id": asset.asset_id,
                        "source": "mock",
                    },
                    allow_direct_notify=False,
                )
            )
        await context.output.say(
            f"已找到{object_name}，mock 结果显示它在前方" if mock_found else f"暂时没有找到{object_name}",
            priority="normal",
        )
        await context.complete(
            {
                "object_name": object_name,
                "found": mock_found,
                "confidence": confidence,
                "asset_id": asset.asset_id,
            },
            summary="找物完成" if mock_found else "找物未命中",
        )

    async def on_cancel(self, context: TaskContext) -> None:
        """取消找物视觉任务。"""

        _ = context
